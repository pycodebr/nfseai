"""DPS (Declaração de Prestação de Serviço) do caminho A: montagem do XML e validação no XSD oficial.

O leiaute segue os esquemas XSD 1.01 e as regras de negócio do Anexo I (SEFIN/ADN, 09/02/2026), copiados
sem alteração em ``schemas/1.01``. A DPS montada aqui ainda não está assinada: a assinatura é feita depois,
com o certificado do emitente, e o XML só segue para a Sefin Nacional assinado.

Como no caminho B, só o cenário mapeado é aceito. O resto é recusado com UnsupportedScenario antes de
qualquer envio. Veja docs/caminho-a.md.
"""
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from lxml import etree

from .config import only_digits, validate_document
from .errors import NfseValidationError, UnsupportedScenario

NS = 'http://www.sped.fazenda.gov.br/nfse'
VERSAO = '1.01'
SCHEMAS = Path(__file__).parent / 'schemas' / VERSAO

AMBIENTE_PRODUCAO = 1
AMBIENTE_HOMOLOGACAO = 2

# Dois pacotes de esquemas da mesma versão 1.01 convivem enquanto a Nota Técnica 009 (reforma tributária)
# não chega à produção: o publicado em 09/02/2026, em vigor na produção, e o da NT 009 (pacote de 24/11/2026),
# que a produção restrita aplica desde 20/10/2026 e com o qual recusa a DPS anterior (E1235).
LEIAUTE_PUBLICADO = '1.01'
LEIAUTE_NT009 = '1.01-nt009'
LEIAUTES = (LEIAUTE_PUBLICADO, LEIAUTE_NT009)
LEIAUTE_POR_AMBIENTE = {AMBIENTE_PRODUCAO: LEIAUTE_PUBLICADO, AMBIENTE_HOMOLOGACAO: LEIAUTE_NT009}
FINALIDADE_REGULAR = 0  # finNFSe: NFS-e regular (NT 009)
DESTINATARIO_E_O_TOMADOR = 0  # indDest: o destinatário é o próprio tomador (NT 009)
SERIE_MIN, SERIE_MAX = 1, 49999  # faixa oficial de "emissão com aplicativo próprio"
NUMERO_MAX = 999999999999999

OPCAO_NAO_OPTANTE = 1
REGIME_ESPECIAL_NENHUM = 0
TP_EMIT_PRESTADOR = 1
TRIB_ISSQN_TRIBUTAVEL = 1
RET_ISSQN_NAO_RETIDO = 1
RET_ISSQN_TOMADOR = 2


@dataclass(frozen=True)
class Emitente:
    """Prestador que emite a DPS (o dono do certificado).

    `codigo_municipio` é o município emissor (IBGE, 7 dígitos). `inscricao_municipal` é obrigatória quando
    o município tem cadastro complementar do contribuinte no CNC (regra E0120 do Anexo I).
    """

    cnpj: str
    codigo_municipio: str
    inscricao_municipal: str = ''
    opcao_simples: int = OPCAO_NAO_OPTANTE
    regime_especial: int = REGIME_ESPECIAL_NENHUM


def dps_id(codigo_municipio, inscricao, serie, numero):
    """Identificador da DPS.

    "DPS" + município emissor (7) + tipo de inscrição (1 CPF, 2 CNPJ) + inscrição (14) + série (5) + número (15).
    """
    digits = only_digits(inscricao)
    tipo = '1' if len(digits) == 11 else '2'
    return f'DPS{codigo_municipio}{tipo}{digits.zfill(14)}{int(serie):05d}{int(numero):015d}'


def _dec(value):
    return f'{Decimal(value).quantize(Decimal("0.01"))}'


def _sub(parent, tag, text=None):
    element = etree.SubElement(parent, f'{{{NS}}}{tag}')
    if text is not None:
        element.text = str(text)
    return element


def _check_scenario(request, emitente, serie, numero):
    tributacao = request.tributacao
    if emitente.opcao_simples != OPCAO_NAO_OPTANTE:
        raise UnsupportedScenario(
            'o caminho A cobre, por enquanto, emitente não optante do Simples Nacional; para o Simples, use o '
            'caminho B (portal)'
        )
    if emitente.regime_especial != REGIME_ESPECIAL_NENHUM:
        raise UnsupportedScenario('regime especial de tributação ainda não mapeado no caminho A')
    if tributacao.preencher_ibs_cbs:
        raise UnsupportedScenario('informações IBS/CBS ainda não mapeadas no caminho A')
    if not SERIE_MIN <= int(serie) <= SERIE_MAX:
        raise NfseValidationError(
            f'série da DPS fora da faixa de aplicativo próprio ({SERIE_MIN} a {SERIE_MAX}): {serie}', ['série']
        )
    if not 1 <= int(numero) <= NUMERO_MAX:
        raise NfseValidationError(f'número da DPS inválido: {numero}', ['número'])
    if tributacao.tributos_aproximados is None:
        raise NfseValidationError(
            'informe os percentuais aproximados dos tributos (federal, estadual e municipal): quem não é do '
            'Simples Nacional não pode deixar de informar o total aproximado (regra E0713)',
            ['tributos aproximados'],
        )
    if tributacao.reter_issqn and request.tomador.endereco is None:
        raise NfseValidationError(
            'a retenção do ISSQN pelo tomador exige o endereço do tomador na DPS (regra E0237)', ['endereço']
        )
    if Decimal(request.valor) <= 0:
        raise NfseValidationError('o valor do serviço deve ser maior que zero', ['valor'])


def _pessoa(parent, documento, nome, endereco, email, telefone):
    digits = validate_document(documento)
    _sub(parent, 'CNPJ' if len(digits) == 14 else 'CPF', digits)
    _sub(parent, 'xNome', nome)
    if endereco is not None:
        end = _sub(parent, 'end')
        nac = _sub(end, 'endNac')
        _sub(nac, 'cMun', endereco.codigo_municipio)
        _sub(nac, 'CEP', only_digits(endereco.cep))
        _sub(end, 'xLgr', endereco.logradouro)
        _sub(end, 'nro', endereco.numero)
        if endereco.complemento:
            _sub(end, 'xCpl', endereco.complemento)
        _sub(end, 'xBairro', endereco.bairro)
    if telefone:
        _sub(parent, 'fone', only_digits(telefone))
    if email:
        _sub(parent, 'email', email)


def leiaute_padrao(ambiente):
    """Leiaute que o ambiente aplica hoje: NT 009 na produção restrita, o publicado na produção."""
    return LEIAUTE_POR_AMBIENTE[int(ambiente)]


def _conferir_leiaute(leiaute):
    if leiaute not in LEIAUTES:
        raise ValueError(f'leiaute desconhecido: {leiaute!r} (use {", ".join(LEIAUTES)})')
    return leiaute


def build_dps(request, *, emitente, serie, numero, ambiente, emitido_em, versao_aplicativo='nfseai', leiaute=None):
    """Monta a DPS (sem assinatura) e devolve o XML em bytes, já validado no XSD.

    ``leiaute`` escolhe o pacote de esquemas; sem ele, vale o do ambiente (`leiaute_padrao`). No leiaute da
    NT 009 a DPS ganha ``finNFSe`` (nota regular) e ``indDest`` (o destinatário é o tomador), e o ``cNBS``
    passa para antes da descrição do serviço.
    """
    leiaute = _conferir_leiaute(leiaute or leiaute_padrao(ambiente))
    nt009 = leiaute == LEIAUTE_NT009
    if emitido_em.tzinfo is None:
        raise ValueError('emitido_em precisa de fuso horário')
    _check_scenario(request, emitente, serie, numero)
    tributacao = request.tributacao
    servico = request.servico
    tomador = request.tomador

    root = etree.Element(f'{{{NS}}}DPS', nsmap={None: NS}, versao=VERSAO)
    inf = _sub(root, 'infDPS')
    inf.set('Id', dps_id(emitente.codigo_municipio, emitente.cnpj, serie, numero))
    _sub(inf, 'tpAmb', ambiente)
    _sub(inf, 'dhEmi', emitido_em.replace(microsecond=0).isoformat())
    _sub(inf, 'verAplic', versao_aplicativo[:20])
    _sub(inf, 'serie', f'{int(serie):05d}')  # com zeros, igual ao trecho da série no identificador
    _sub(inf, 'nDPS', int(numero))
    _sub(inf, 'dCompet', request.competencia.isoformat())
    _sub(inf, 'tpEmit', TP_EMIT_PRESTADOR)
    if nt009:
        _sub(inf, 'finNFSe', FINALIDADE_REGULAR)
    _sub(inf, 'cLocEmi', emitente.codigo_municipio)

    # Emitente prestador: sem nome nem endereço (regra E0121); o cadastro vem do CNC.
    prest = _sub(inf, 'prest')
    _sub(prest, 'CNPJ', only_digits(emitente.cnpj))
    if emitente.inscricao_municipal:
        _sub(prest, 'IM', emitente.inscricao_municipal)
    reg = _sub(prest, 'regTrib')
    _sub(reg, 'opSimpNac', emitente.opcao_simples)
    _sub(reg, 'regEspTrib', emitente.regime_especial)

    toma = _sub(inf, 'toma')
    _pessoa(toma, tomador.documento, tomador.nome, tomador.endereco, tomador.email, tomador.telefone)
    if nt009:
        _sub(inf, 'indDest', DESTINATARIO_E_O_TOMADOR)

    serv = _sub(inf, 'serv')
    loc = _sub(serv, 'locPrest')
    _sub(loc, 'cLocPrestacao', servico.codigo_municipio_prestacao or emitente.codigo_municipio)
    cserv = _sub(serv, 'cServ')
    _sub(cserv, 'cTribNac', only_digits(servico.codigo_tributacao_nacional))
    if nt009 and servico.codigo_nbs:
        _sub(cserv, 'cNBS', servico.codigo_nbs)
    _sub(cserv, 'xDescServ', servico.descricao.strip())
    if not nt009 and servico.codigo_nbs:
        _sub(cserv, 'cNBS', servico.codigo_nbs)
    if servico.informacoes_complementares:
        compl = _sub(serv, 'infoCompl')
        _sub(compl, 'xInfComp', servico.informacoes_complementares)

    valores = _sub(inf, 'valores')
    vserv = _sub(valores, 'vServPrest')
    _sub(vserv, 'vServ', _dec(request.valor))
    trib = _sub(valores, 'trib')
    mun = _sub(trib, 'tribMun')
    _sub(mun, 'tribISSQN', TRIB_ISSQN_TRIBUTAVEL)
    _sub(mun, 'tpRetISSQN', RET_ISSQN_TOMADOR if tributacao.reter_issqn else RET_ISSQN_NAO_RETIDO)
    if tributacao.aliquota_iss is not None:
        _sub(mun, 'pAliq', _dec(tributacao.aliquota_iss))
    if tributacao.retencao_irrf is not None:
        irrf = Decimal(tributacao.retencao_irrf)
        if not Decimal('0') < irrf < Decimal(request.valor):
            raise NfseValidationError('o IRRF retido deve ser maior que zero e menor que o valor do serviço',
                                      ['retencao_irrf'])
        fed = _sub(trib, 'tribFed')
        _sub(fed, 'vRetIRRF', _dec(irrf))
    tot = _sub(trib, 'totTrib')
    perc = _sub(tot, 'pTotTrib')
    aprox = tributacao.tributos_aproximados
    _sub(perc, 'pTotTribFed', _dec(aprox.federal))
    _sub(perc, 'pTotTribEst', _dec(aprox.estadual))
    _sub(perc, 'pTotTribMun', _dec(aprox.municipal))

    xml = etree.tostring(root, xml_declaration=False, encoding='utf-8')
    validar_dps(xml, leiaute=leiaute)
    return xml


@lru_cache(maxsize=8)
def carregar_schema(nome='DPS_v1.01.xsd', leiaute=LEIAUTE_PUBLICADO):
    """XSD oficial 1.01 do ``leiaute`` informado, com uma correção feita só em memória no pacote publicado.

    O ``tiposSimples_v1.01.xsd`` oficial escreve o padrão da série como ``^0{0,4}\\d{1,5}$``. Em XML Schema,
    ``^`` e ``$`` não são âncoras (o padrão já vale para o valor inteiro): são caracteres literais, e um
    validador estrito como o libxml2 recusa toda série. O validador da Sefin trata os dois como âncoras.
    A correção remove só esses dois caracteres; os arquivos em ``schemas/`` ficam iguais aos publicados. O
    pacote da NT 009 já traz o padrão corrigido e é carregado como veio.
    """
    pasta = SCHEMAS.parent / _conferir_leiaute(leiaute)
    if leiaute == LEIAUTE_NT009:
        return etree.XMLSchema(etree.parse(str(pasta / nome)))
    simples = (pasta / 'tiposSimples_v1.01.xsd').read_bytes()
    defeito = rb'<xs:pattern value="^0{0,4}\d{1,5}$"/>'
    if defeito not in simples:
        raise NfseValidationError('o XSD empacotado não é o publicado na versão 1.01', ['XSD'])
    corrigido = simples.replace(defeito, rb'<xs:pattern value="0{0,4}\d{1,5}"/>')

    class _Resolver(etree.Resolver):
        def resolve(self, url, pubid, context):
            if url.endswith('tiposSimples_v1.01.xsd'):
                return self.resolve_string(corrigido, context, base_url=str(SCHEMAS / 'tiposSimples_v1.01.xsd'))
            return None

    parser = etree.XMLParser()
    parser.resolvers.add(_Resolver())
    return etree.XMLSchema(etree.parse(str(SCHEMAS / nome), parser))


def validar_xml(xml, nome, documento, leiaute=LEIAUTE_PUBLICADO):
    """Valida ``xml`` contra o esquema ``nome``. Lança NfseValidationError com os erros do esquema."""
    try:
        parsed = etree.fromstring(xml)
    except etree.XMLSyntaxError as exc:
        raise NfseValidationError(f'{documento} com XML malformado: {exc}', [str(exc)]) from None
    schema = carregar_schema(nome, leiaute)
    if not schema.validate(parsed):
        errors = [f'linha {e.line}: {e.message}' for e in schema.error_log]
        raise NfseValidationError(f'{documento} recusado pelo XSD {leiaute}: {"; ".join(errors)}', errors)
    return True


def validar_dps(xml, leiaute=None):
    """Valida a DPS contra o XSD oficial 1.01. Sem ``leiaute``, usa o que a própria DPS indica: com
    ``finNFSe`` direto no ``infDPS``, o da NT 009; sem ele, o publicado."""
    if leiaute is None:
        try:
            inf = etree.fromstring(xml).find(f'{{{NS}}}infDPS')
        except etree.XMLSyntaxError:
            inf = None
        nt009 = inf is not None and inf.find(f'{{{NS}}}finNFSe') is not None
        leiaute = LEIAUTE_NT009 if nt009 else LEIAUTE_PUBLICADO
    return validar_xml(xml, 'DPS_v1.01.xsd', 'DPS', leiaute)


__all__ = [
    'LEIAUTE_NT009', 'LEIAUTE_PUBLICADO', 'LEIAUTES', 'NS', 'VERSAO', 'Emitente', 'build_dps', 'carregar_schema',
    'dps_id', 'leiaute_padrao', 'validar_dps', 'validar_xml',
]
