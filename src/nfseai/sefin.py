"""Cliente da API da Sefin Nacional (caminho A): emissão, consulta e cancelamento com certificado A1.

Expõe as mesmas partes que ``EmissorNacionalClient`` (``emissao``, ``notas``, ``cancelamento``), para que
``operations.py`` funcione igual nos dois caminhos. As rotas vêm do Manual dos Contribuintes (APIs do Emissor
Público Nacional, v1.0). Os nomes dos campos JSON estão reunidos em ``CAMPOS`` e foram conferidos no
Swagger da Sefin (produção restrita, 26/11/2026). Veja docs/caminho-a.md.

Diferenças em relação ao portal:

- o ensaio (``prepare``) não envia nada: monta, valida no XSD e assina a DPS localmente;
- o número da DPS é reservado (``numerador``) só na emissão, antes do envio;
- a API tem idempotência própria: se o resultado do envio ficar incerto, ``GET /dps/{id}`` diz se a nota
  existe. Mesmo assim, o envio nunca é repetido automaticamente.
"""
import base64
import datetime as dt
import gzip
from dataclasses import dataclass, field
from decimal import Decimal

from lxml import etree

from .assinatura import assinar
from .config import only_digits
from .dps import NS, VERSAO, build_dps, dps_id, leiaute_padrao, validar_xml
from .errors import (
    CancellationNotAllowed,
    ConfirmationRequired,
    EmissionRejectedError,
    EmissionUncertainError,
    NfseHttpError,
    NfseValidationError,
)
from .models import EVENTO_CANCELAMENTO, CancelPreview, CancelResult, EmissionResult, PreparedEmission
from .parsing import CHAVE_RE

AMBIENTES = {
    'producao-restrita': ('https://sefin.producaorestrita.nfse.gov.br/SefinNacional', 2),
    'producao': ('https://sefin.nfse.gov.br/SefinNacional', 1),
}

# Nomes dos campos JSON da API (mapeados da documentação de terceiros; conferir no Swagger da Sefin).
CAMPOS = {
    'dps': 'dpsXmlGZipB64',
    'nfse': 'nfseXmlGZipB64',
    'chave': 'chaveAcesso',
    'pedido_evento': 'pedidoRegistroEventoXmlGZipB64',
    'evento': 'eventoXmlGZipB64',
    'erros': ('erros', 'Erros'),
    'erro': 'erro',  # resposta de erro dos eventos (ResponseErro): um objeto só
}

MOTIVOS_CANCELAMENTO = {'1': 'Erro na emissão', '2': 'Serviço não prestado', '9': 'Outros'}
SITUACAO_GERADA = 'NFS-e gerada'
SITUACAO_CANCELADA = 'NFS-e cancelada'
BRT = dt.timezone(dt.timedelta(hours=-3))
N = {'n': NS}


def gzip_b64(data):
    return base64.b64encode(gzip.compress(data)).decode()


def ungzip_b64(text):
    return gzip.decompress(base64.b64decode(text))


@dataclass(frozen=True)
class SefinResponse:
    status_code: int
    data: dict = field(default_factory=dict)


def _erros(data):
    for chave in CAMPOS['erros']:
        lista = (data or {}).get(chave)
        if lista:
            break
    else:
        lista = [(data or {})[CAMPOS['erro']]] if (data or {}).get(CAMPOS['erro']) else []
        if not lista:
            return []
    erros = []
    for erro in lista:
        if not isinstance(erro, dict):
            erros.append(str(erro))
            continue
        codigo = erro.get('Codigo') or erro.get('codigo') or ''
        descricao = erro.get('Descricao') or erro.get('descricao') or ''
        complemento = erro.get('Complemento') or erro.get('complemento') or ''
        erros.append(f'{codigo}: {descricao}' + (f' ({complemento})' if complemento else ''))
    return erros


# Recusas com causa conhecida e solução que não depende do contador. A lista `errors` da exceção continua com o
# texto da Sefin; a dica vai na mensagem e em `exc.dicas`.
DICAS_REJEICAO = {
    'E0014': (
        'E0014: já existe DPS com esta série e este número. Em geral, outro sistema da empresa (ERP, emissor '
        'anterior) usa a mesma série, ou o registro local (ledger.sqlite3) foi recriado e a numeração recomeçou. '
        'Escolha uma série que só o nfseai use: `nfseai configurar --serie N`. Não reenvie a mesma nota.'
    ),
    'E1235': (
        'E1235: a DPS não segue o leiaute que o ambiente aplica (virada da NT 009). Veja o leiaute em '
        '`nfseai status` e troque com `nfseai configurar --leiaute 1.01-nt009` (ou `--leiaute 1.01`).'
    ),
}


def dicas_rejeicao(erros):
    """Orientações para os códigos de recusa conhecidos, na ordem em que aparecem."""
    dicas = []
    for erro in erros:
        codigo = str(erro).split(':', 1)[0].strip()
        dica = DICAS_REJEICAO.get(codigo)
        if dica and dica not in dicas:
            dicas.append(dica)
    return dicas


# --------------------------------------------------------------------------- sessão
class SefinSession:
    """HTTPS com TLS mútuo (certificado A1 do emitente) e HTTP/1.1, com ritmo mínimo entre chamadas.

    ``get`` e ``post`` devolvem ``SefinResponse`` para qualquer status HTTP; só falha de rede (sem
    resposta) vira ``NfseHttpError``, porque nesse caso não se sabe se a Sefin processou o pedido.
    """

    def __init__(self, certificado, ambiente='producao-restrita', *, pasta_privada, timeout=60.0,
                 min_interval=0.35, http_client=None, cafile=None):
        import httpx

        if ambiente not in AMBIENTES:
            raise ValueError(f'ambiente desconhecido: {ambiente!r} (use {", ".join(AMBIENTES)})')
        self.base_url, self.tipo_ambiente = AMBIENTES[ambiente]
        self._min_interval = min_interval
        self._last = 0.0
        self._httpx = httpx
        self._client = http_client or httpx.Client(
            base_url=self.base_url,
            verify=certificado.contexto_tls(pasta_privada, cafile=cafile),
            http2=False,
            timeout=timeout,
            headers={'Accept': 'application/json', 'User-Agent': 'nfseai'},
        )

    def _send(self, method, path, **kwargs):
        import time

        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            response = self._client.request(method, path, **kwargs)
        except self._httpx.HTTPError as exc:
            raise NfseHttpError(f'falha de rede em {method} {path}: {exc}', method=method, url=path) from exc
        finally:
            self._last = time.monotonic()
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {'texto': response.text[:500]}
        return SefinResponse(response.status_code, data if isinstance(data, dict) else {'lista': data})

    def get(self, path):
        return self._send('GET', path)

    def post(self, path, payload):
        return self._send('POST', path, json=payload)

    def close(self):
        self._client.close()


# --------------------------------------------------------------------------- nota
@dataclass(frozen=True)
class NotaSefin:
    """NFS-e lida do XML oficial devolvido pela Sefin. Os nomes seguem ``NotaDetalhe`` do caminho B."""

    chave_acesso: str
    numero_nfse: str
    serie_dps: str
    numero_dps: str
    data_geracao: str
    competencia: str
    emitente_documento: str
    tomador_documento: str
    tomador_nome: str
    valor_servico: Decimal | None
    valor_liquido: Decimal | None
    cancelada: bool = False
    xml: bytes = field(default=b'', repr=False)

    @property
    def situacao(self):
        return SITUACAO_CANCELADA if self.cancelada else SITUACAO_GERADA


def ler_nfse(xml, *, cancelada=False):
    raiz = etree.fromstring(xml)
    inf = raiz.find('n:infNFSe', N)
    if inf is None:
        raise NfseValidationError('o XML devolvido não é uma NFS-e', ['NFS-e'])
    dps = inf.find('n:DPS/n:infDPS', N)

    def texto(node, caminho):
        return (node.findtext(caminho, namespaces=N) or '').strip() if node is not None else ''

    valor = texto(dps, 'n:valores/n:vServPrest/n:vServ')
    liquido = texto(inf, 'n:valores/n:vLiq')
    return NotaSefin(
        chave_acesso=(inf.get('Id') or '')[3:],
        numero_nfse=texto(inf, 'n:nNFSe'),
        serie_dps=texto(dps, 'n:serie'),
        numero_dps=texto(dps, 'n:nDPS'),
        data_geracao=texto(inf, 'n:dhProc'),
        competencia=texto(dps, 'n:dCompet'),
        emitente_documento=texto(inf, 'n:emit/n:CNPJ') or texto(inf, 'n:emit/n:CPF'),
        tomador_documento=texto(dps, 'n:toma/n:CNPJ') or texto(dps, 'n:toma/n:CPF'),
        tomador_nome=texto(dps, 'n:toma/n:xNome'),
        valor_servico=Decimal(valor) if valor else None,
        valor_liquido=Decimal(liquido) if liquido else None,
        cancelada=cancelada,
        xml=xml,
    )


# --------------------------------------------------------------------------- partes
class NotasSefin:
    def __init__(self, session):
        self._session = session

    def cancelada(self, chave_acesso):
        # Rota conferida no Swagger: /nfse/{chave}/eventos/{tipoEvento}/{numSeqEvento}; o cancelamento é o 1º.
        response = self._session.get(f'/nfse/{chave_acesso}/eventos/{EVENTO_CANCELAMENTO}/1')
        if response.status_code == 404:
            return False
        if response.status_code == 200:
            return True
        raise NfseHttpError(
            f'consulta de eventos da nota {chave_acesso} retornou HTTP {response.status_code}',
            status_code=response.status_code, method='GET', url=f'/nfse/{chave_acesso}/eventos',
        )

    def get(self, chave_acesso):
        """NFS-e pela chave de acesso de 50 dígitos, com o XML oficial."""
        if not CHAVE_RE.fullmatch(chave_acesso or ''):
            raise NfseValidationError('chave de acesso deve ter 50 dígitos', ['chave inválida'])
        response = self._session.get(f'/nfse/{chave_acesso}')
        if response.status_code != 200 or not response.data.get(CAMPOS['nfse']):
            raise NfseHttpError(
                f'a Sefin não devolveu a nota {chave_acesso} (HTTP {response.status_code}): '
                f'{"; ".join(_erros(response.data))}',
                status_code=response.status_code, method='GET', url=f'/nfse/{chave_acesso}',
            )
        nota = ler_nfse(ungzip_b64(response.data[CAMPOS['nfse']]), cancelada=self.cancelada(chave_acesso))
        if nota.chave_acesso != chave_acesso:
            raise NfseValidationError('a Sefin devolveu uma nota diferente da solicitada', ['chave'])
        return nota

    def download_xml(self, chave_acesso):
        """XML oficial da NFS-e (no caminho A não há captcha)."""
        return self.get(chave_acesso).xml

    def chave_da_dps(self, id_dps):
        """Chave da NFS-e gerada a partir de uma DPS, ou None se a Sefin não tem nota para ela.

        A rota recebe os 42 números do identificador, sem o prefixo ``DPS`` (com ele, a Sefin responde E2400).
        """
        id_dps = (id_dps or '').strip()
        id_dps = id_dps[3:] if id_dps.upper().startswith('DPS') else id_dps
        response = self._session.get(f'/dps/{id_dps}')
        if response.status_code == 200 and response.data.get(CAMPOS['chave']):
            return response.data[CAMPOS['chave']]
        if response.status_code == 404:
            return None
        raise NfseHttpError(
            f'consulta da DPS {id_dps} retornou HTTP {response.status_code}',
            status_code=response.status_code, method='GET', url=f'/dps/{id_dps}',
        )


class EmissaoSefin:
    def __init__(self, client):
        self._c = client

    def _resumo(self, request):
        tributacao = request.tributacao
        aprox = tributacao.tributos_aproximados
        return {
            'Emitente': {'CNPJ': self._c.emitente.cnpj, 'Município emissor': self._c.emitente.codigo_municipio},
            'Tomador': {'Documento': only_digits(request.tomador.documento), 'Nome': request.tomador.nome},
            'Serviço': {
                'Código de tributação nacional': request.servico.codigo_tributacao_nacional,
                'NBS': request.servico.codigo_nbs,
                'Descrição': request.servico.descricao,
            },
            'Valores': self._valores(request),
            'Tributação': {
                'ISSQN retido pelo tomador': 'sim' if tributacao.reter_issqn else 'não',
                'IRRF retido': str(tributacao.retencao_irrf) if tributacao.retencao_irrf is not None else 'não',
                'Alíquota do ISSQN informada': str(tributacao.aliquota_iss) if tributacao.aliquota_iss else 'não',
                'Tributos aproximados (%)': (
                    f'federal {aprox.federal}, estadual {aprox.estadual}, municipal {aprox.municipal}' if aprox else ''
                ),
            },
            'Competência': request.competencia.isoformat(),
        }

    @staticmethod
    def _valores(request):
        centavos = Decimal('0.01')
        valor = Decimal(request.valor).quantize(centavos)
        valores = {'Valor do serviço': str(valor)}
        irrf = request.tributacao.retencao_irrf
        if irrf is not None:
            # O ISSQN retido sai do líquido também, mas o valor dele depende da alíquota do município, que a
            # Sefin aplica; o resumo só desconta o que a própria DPS informa.
            rotulo = 'Valor líquido (antes do ISSQN retido)' if request.tributacao.reter_issqn else 'Valor líquido'
            valores[rotulo] = str((valor - Decimal(irrf)).quantize(centavos))
        return valores

    def _montar(self, request, serie, numero):
        xml = build_dps(
            request, emitente=self._c.emitente, serie=serie, numero=numero, ambiente=self._c.tipo_ambiente,
            emitido_em=self._c.agora(), versao_aplicativo=self._c.versao_aplicativo, leiaute=self._c.leiaute,
        )
        return assinar(xml, self._c.certificado)

    def prepare(self, request):
        """Ensaio: confere o certificado, monta, valida no XSD e assina a DPS. Nada é enviado à Sefin."""
        self._c.certificado.conferir(self._c.emitente.cnpj, agora=self._c.agora())
        if not (request.tomador.nome or '').strip():
            raise NfseValidationError('informe o nome do tomador: a API não consulta a Receita', ['nome'])
        self._montar(request, 1, 1)
        return PreparedEmission(
            draft_id='', request=request, tomador_nome=request.tomador.nome, resumo=self._resumo(request)
        )

    def discard(self, prepared):
        """No caminho A o ensaio não cria rascunho no servidor; não há o que excluir."""
        return True

    def _conferir(self, nota, request):
        problemas = []
        if nota.emitente_documento != only_digits(self._c.emitente.cnpj):
            problemas.append('emitente')
        if nota.tomador_documento != only_digits(request.tomador.documento):
            problemas.append('tomador')
        if nota.valor_servico != Decimal(request.valor).quantize(Decimal('0.01')):
            problemas.append('valor')
        if nota.competencia != request.competencia.isoformat():
            problemas.append('competência')
        return problemas

    def _resultado(self, nota, id_dps, request):
        problemas = self._conferir(nota, request)
        if problemas:
            raise EmissionUncertainError(
                f'a nota {nota.chave_acesso} foi gerada, mas {", ".join(problemas)} não conferem com o '
                'solicitado. Confira a nota antes de qualquer nova ação.',
                draft_id=id_dps,
            )
        return EmissionResult(chave_acesso=nota.chave_acesso, draft_id=id_dps, final_url='/nfse', nota=nota)

    def _recuperar(self, id_dps, request, causa):
        try:
            chave = self._c.notas.chave_da_dps(id_dps)
        except NfseHttpError:
            chave = None
        if not chave:
            raise EmissionUncertainError(
                f'não foi possível confirmar a emissão da DPS {id_dps} ({causa}). A Sefin ainda não mostra nota '
                'para ela. Não reenvie: consulte a DPS de novo mais tarde antes de qualquer nova ação.',
                draft_id=id_dps,
            ) from causa
        return self._resultado(self._c.notas.get(chave), id_dps, request)

    def emit(self, prepared, *, confirm=False, permitir_duplicidade=False):
        """Emite a NFS-e. Irreversível (só cancelamento posterior). Nunca reenvia a mesma emissão.

        ``permitir_duplicidade`` existe para manter a assinatura do caminho B. A API não lista notas por
        tomador e valor; o controle contra nota em dobro é o registro por referência somado ao
        identificador da DPS.
        """
        if not confirm:
            raise ConfirmationRequired('emitir NFS-e exige confirm=True')
        request = prepared.request
        self._c.certificado.conferir(self._c.emitente.cnpj, agora=self._c.agora())
        serie, numero = self._c.numerador(request)
        id_dps = dps_id(self._c.emitente.codigo_municipio, self._c.emitente.cnpj, serie, numero)
        xml = self._montar(request, serie, numero)
        try:
            response = self._c.session.post('/nfse', {CAMPOS['dps']: gzip_b64(xml)})
        except NfseHttpError as exc:
            return self._recuperar(id_dps, request, exc)
        if response.status_code in (200, 201) and response.data.get(CAMPOS['nfse']):
            return self._resultado(ler_nfse(ungzip_b64(response.data[CAMPOS['nfse']])), id_dps, request)
        erros = _erros(response.data)
        if erros and 400 <= response.status_code < 500:
            dicas = dicas_rejeicao(erros)
            mensagem = f'a Sefin recusou a DPS {id_dps}: {"; ".join(erros)}'
            if dicas:
                mensagem += ' | o que fazer: ' + ' '.join(dicas)
            exc = EmissionRejectedError(mensagem, erros)
            exc.dicas = dicas
            raise exc
        return self._recuperar(id_dps, request, f'HTTP {response.status_code}')


class CancelamentoSefin:
    def __init__(self, client):
        self._c = client

    def preview(self, chave_acesso):
        """Confere se a nota existe e está ativa. Não envia nada."""
        nota = self._c.notas.get(chave_acesso)
        if nota.cancelada:
            raise CancellationNotAllowed(f'a nota {chave_acesso} já está cancelada')
        return CancelPreview(
            chave_acesso=chave_acesso, tipo_evento=EVENTO_CANCELAMENTO, mensagem='',
            form_action=f'/nfse/{chave_acesso}/eventos', fields={}, motivos=dict(MOTIVOS_CANCELAMENTO),
        )

    def _pedido(self, chave_acesso, motivo, justificativa):
        raiz = etree.Element(f'{{{NS}}}pedRegEvento', nsmap={None: NS}, versao=VERSAO)
        inf = etree.SubElement(raiz, f'{{{NS}}}infPedReg', Id=f'PRE{chave_acesso}{EVENTO_CANCELAMENTO}')
        valores = (
            ('tpAmb', self._c.tipo_ambiente),
            ('verAplic', self._c.versao_aplicativo),
            ('dhEvento', self._c.agora().replace(microsecond=0).isoformat()),
            ('CNPJAutor', only_digits(self._c.emitente.cnpj)),
            ('chNFSe', chave_acesso),
        )
        for tag, valor in valores:
            etree.SubElement(inf, f'{{{NS}}}{tag}').text = str(valor)
        evento = etree.SubElement(inf, f'{{{NS}}}e{EVENTO_CANCELAMENTO}')
        etree.SubElement(evento, f'{{{NS}}}xDesc').text = 'Cancelamento de NFS-e'
        etree.SubElement(evento, f'{{{NS}}}cMotivo').text = str(motivo)
        etree.SubElement(evento, f'{{{NS}}}xMotivo').text = justificativa
        xml = assinar(etree.tostring(raiz, encoding='utf-8'), self._c.certificado, no='infPedReg')
        validar_pedido_evento(xml)
        return xml

    def cancel(self, chave_acesso, *, motivo, justificativa, confirm=False, permitir_analise_fiscal=False):
        """Cancela a NFS-e (evento 101101). Motivos: '1' erro na emissão, '2' serviço não prestado, '9' outros."""
        justificativa = (justificativa or '').strip()
        if not 15 <= len(justificativa) <= 255:
            raise NfseValidationError('a justificativa deve ter de 15 a 255 caracteres', ['justificativa'])
        if not confirm:
            raise ConfirmationRequired('cancelar NFS-e exige confirm=True')
        if str(motivo) not in MOTIVOS_CANCELAMENTO:
            raise NfseValidationError(f'motivo inválido; opções: {MOTIVOS_CANCELAMENTO}', ['motivo'])
        self._c.certificado.conferir(self._c.emitente.cnpj, agora=self._c.agora())
        self.preview(chave_acesso)
        pedido = self._pedido(chave_acesso, motivo, justificativa)
        response = self._c.session.post(f'/nfse/{chave_acesso}/eventos', {CAMPOS['pedido_evento']: gzip_b64(pedido)})
        if response.status_code not in (200, 201):
            erros = _erros(response.data)
            raise NfseValidationError(
                f'cancelamento não processado (HTTP {response.status_code}): {"; ".join(erros)}', erros
            )
        nota = self._c.notas.get(chave_acesso)
        if not nota.cancelada:
            raise NfseHttpError(
                f'a Sefin aceitou o cancelamento da nota {chave_acesso}, mas o evento ainda não aparece; '
                'confira antes de qualquer nova ação',
                status_code=response.status_code, method='POST', url=f'/nfse/{chave_acesso}/eventos',
            )
        return CancelResult(
            chave_acesso=chave_acesso, tipo_evento=EVENTO_CANCELAMENTO, sucesso=True, mensagem='',
            situacao_apos=nota.situacao,
        )


def validar_pedido_evento(xml):
    """Valida o pedido de registro de evento contra o XSD oficial 1.01."""
    return validar_xml(xml, 'pedRegEvento_v1.01.xsd', 'pedido de registro de evento')


# --------------------------------------------------------------------------- cliente
class SefinClient:
    """Ponto de entrada do caminho A: ``client.emissao``, ``client.notas`` e ``client.cancelamento``.

    ``numerador(request) -> (serie, numero)`` reserva o número da DPS antes do envio e nunca devolve o
    mesmo número duas vezes. ``relogio`` devolve a data e hora com fuso (padrão: agora, em UTC-3).
    """

    def __init__(self, session, certificado, emitente, *, numerador, ambiente, relogio=None,
                 versao_aplicativo='nfseai', leiaute=None):
        self.session = session
        self.leiaute = leiaute or leiaute_padrao(ambiente)
        self.certificado = certificado
        self.emitente = emitente
        self.numerador = numerador
        self.tipo_ambiente = ambiente
        self.versao_aplicativo = versao_aplicativo
        self._relogio = relogio or (lambda: dt.datetime.now(BRT))
        self.empresa = None
        self.notas = NotasSefin(session)
        self.emissao = EmissaoSefin(self)
        self.cancelamento = CancelamentoSefin(self)

    @classmethod
    def from_profile(cls, empresa=None, *, home=None, env_file=None, store=None, **session_kwargs):
        """Cliente de uma empresa conectada com `nfseai conectar --certificado`.

        A numeração da DPS fica no registro local (``ledger.sqlite3``), na série configurada para a empresa.
        """
        from .ledger import Ledger
        from .profiles import Store

        store = store or Store(home=home, env_file=env_file)
        name = store.resolve(empresa)
        config = store.sefin_config(name)
        certificado = store.load_certificate(name)
        certificado.conferir(config['cnpj'])
        store.ensure_home()
        session = SefinSession(
            certificado, config['ambiente'], pasta_privada=store.private_dir(store.tls_dir), **session_kwargs
        )
        ledger = Ledger(store.ledger_path)
        serie = int(config.get('serie') or 1)

        def numerador(request):
            return serie, ledger.reservar_dps(name, serie, getattr(request, 'referencia', None) or None)

        client = cls(
            session, certificado, store.emitente(name), numerador=numerador, ambiente=session.tipo_ambiente,
            leiaute=config.get('leiaute') or None,
        )
        client.empresa = name
        return client

    def agora(self):
        return self._relogio()

    def close(self):
        close = getattr(self.session, 'close', None)
        if close:
            close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


__all__ = [
    'AMBIENTES', 'CAMPOS', 'DICAS_REJEICAO', 'NotaSefin', 'SefinClient', 'SefinResponse', 'SefinSession',
    'dicas_rejeicao', 'gzip_b64', 'ler_nfse', 'ungzip_b64', 'validar_pedido_evento',
]
