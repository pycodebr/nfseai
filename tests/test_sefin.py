import base64
import datetime as dt
import gzip
from decimal import Decimal

import pytest
from lxml import etree

from certificado_teste import SENHA, gerar_pfx
from nfseai.certificado import CertificadoA1
from nfseai.dps import NS, dps_id, validar_dps
from nfseai.errors import (
    CertificateError,
    ConfirmationRequired,
    EmissionRejectedError,
    EmissionUncertainError,
    NfseHttpError,
    NfseValidationError,
)
from nfseai.sefin import SefinClient, SefinResponse, gzip_b64, validar_pedido_evento
from test_dps import APROXIMADOS, EMITENTE, EMITTER, MUNICIPIO, TOMADOR, pedido

N = {'n': NS}
NUMERO_NFSE = 27
CHAVE = f'{MUNICIPIO}22{EMITTER}{NUMERO_NFSE:013d}2611123456789' + '7'
QUANDO = dt.datetime(2026, 11, 20, 10, 30, tzinfo=dt.timezone(dt.timedelta(hours=-3)))
VALIDADE = {'inicio': dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), 'dias_validade': 3650}


def ungzip(texto):
    return gzip.decompress(base64.b64decode(texto))


def nfse_de(dps_xml, *, chave=CHAVE, numero=NUMERO_NFSE, tomador=None):
    """NFS-e fictícia no leiaute 1.01 envolvendo a DPS recebida, como a Sefin devolveria."""
    dps = etree.fromstring(dps_xml)
    if tomador:
        dps.find('n:infDPS/n:toma/n:CNPJ', N).text = tomador
    raiz = etree.Element(f'{{{NS}}}NFSe', nsmap={None: NS}, versao='1.01')
    inf = etree.SubElement(raiz, f'{{{NS}}}infNFSe', Id=f'NFS{chave}')
    for tag, valor in (('nNFSe', numero), ('cStat', 100), ('dhProc', '2026-11-20T10:30:05-03:00')):
        etree.SubElement(inf, f'{{{NS}}}{tag}').text = str(valor)
    emit = etree.SubElement(inf, f'{{{NS}}}emit')
    etree.SubElement(emit, f'{{{NS}}}CNPJ').text = EMITTER
    etree.SubElement(emit, f'{{{NS}}}xNome').text = 'EMPRESA EXEMPLO LTDA'
    valores = etree.SubElement(inf, f'{{{NS}}}valores')
    etree.SubElement(valores, f'{{{NS}}}vLiq').text = '1500.00'
    inf.append(dps)
    return etree.tostring(raiz)


class Numerador:
    def __init__(self):
        self.reservas = []

    def __call__(self, request):
        self.reservas.append(request.referencia)
        return 1, 41 + len(self.reservas)


class FakeSefin:
    """Substitui SefinSession: responde por rota e registra as chamadas."""

    def __init__(self):
        self.calls = []
        self.routes = {}

    def on(self, method, path, handler):
        self.routes[(method, path)] = handler

    def _dispatch(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        handler = self.routes.get((method, path))
        if handler is None:
            raise AssertionError(f'rota não prevista no teste: {method} {path}')
        result = handler(payload) if callable(handler) else handler
        if isinstance(result, Exception):
            raise result
        return result

    def get(self, path):
        return self._dispatch('GET', path)

    def post(self, path, payload):
        return self._dispatch('POST', path, payload)

    def posts(self):
        return [call for call in self.calls if call[0] == 'POST']


@pytest.fixture(scope='module')
def certificado():
    return CertificadoA1.carregar(gerar_pfx(EMITTER, **VALIDADE), SENHA.decode())


@pytest.fixture
def numerador():
    return Numerador()


@pytest.fixture
def sefin():
    return FakeSefin()


@pytest.fixture
def client(sefin, certificado, numerador):
    return SefinClient(sefin, certificado, EMITENTE, numerador=numerador, ambiente=2, relogio=lambda: QUANDO)


def responder_emissao(sefin, **nfse_kwargs):
    def handler(payload):
        dps = ungzip(payload['dpsXmlGZipB64'])
        return SefinResponse(201, {'chaveAcesso': CHAVE, 'nfseXmlGZipB64': gzip_b64(nfse_de(dps, **nfse_kwargs))})

    sefin.on('POST', '/nfse', handler)
    sefin.on('GET', f'/nfse/{CHAVE}/eventos/101101/1', SefinResponse(404, {}))


def test_ensaio_nao_envia_nada_e_mostra_o_resumo(client, sefin, numerador):
    prepared = client.emissao.prepare(pedido())
    assert sefin.calls == []
    assert numerador.reservas == []
    assert prepared.tomador_nome == 'TOMADORA EXEMPLO LTDA'
    assert prepared.resumo['Valores']['Valor do serviço'] == '1500.00'
    assert prepared.resumo['Tributação']['ISSQN retido pelo tomador'] == 'não'
    assert client.emissao.discard(prepared) is True


def test_emitir_exige_confirmacao(client, sefin):
    with pytest.raises(ConfirmationRequired):
        client.emissao.emit(client.emissao.prepare(pedido()))
    assert sefin.calls == []


def test_emissao_envia_dps_assinada_e_le_a_nota_devolvida(client, sefin, numerador):
    responder_emissao(sefin)
    result = client.emissao.emit(client.emissao.prepare(pedido()), confirm=True)

    assert numerador.reservas == ['pedido-1042']
    [(_, path, payload)] = sefin.posts()
    assert path == '/nfse'
    enviada = ungzip(payload['dpsXmlGZipB64'])
    validar_dps(enviada)
    assert b'<SignatureValue>' in enviada
    assert etree.fromstring(enviada).find('n:infDPS', N).get('Id') == dps_id(MUNICIPIO, EMITTER, 1, 42)
    assert result.chave_acesso == CHAVE
    assert result.draft_id == dps_id(MUNICIPIO, EMITTER, 1, 42)
    nota = result.nota
    assert nota.numero_nfse == str(NUMERO_NFSE)
    assert (nota.serie_dps, nota.numero_dps) == ('00001', '42')
    assert nota.tomador_documento == TOMADOR
    assert nota.valor_servico == Decimal('1500.00')
    assert nota.situacao == 'NFS-e gerada'
    assert nota.xml.startswith(b'<NFSe')


def test_rejeicao_da_sefin_vira_emission_rejected_com_os_codigos(client, sefin):
    sefin.on('POST', '/nfse', SefinResponse(400, {'erros': [
        {'Codigo': 'E0160', 'Descricao': 'Opção do Simples Nacional não confere.'},
    ]}))
    with pytest.raises(EmissionRejectedError, match='E0160') as exc:
        client.emissao.emit(client.emissao.prepare(pedido()), confirm=True)
    assert exc.value.errors == ['E0160: Opção do Simples Nacional não confere.']
    assert exc.value.dicas == []


@pytest.mark.parametrize('codigo, comando', [('E0014', '--serie'), ('E1235', '--leiaute')])
def test_recusa_conhecida_traz_o_que_fazer_sem_mudar_os_erros(client, sefin, codigo, comando):
    sefin.on('POST', '/nfse', SefinResponse(400, {'erros': [{'Codigo': codigo, 'Descricao': 'descrição oficial'}]}))
    with pytest.raises(EmissionRejectedError) as exc:
        client.emissao.emit(client.emissao.prepare(pedido()), confirm=True)
    assert exc.value.errors == [f'{codigo}: descrição oficial']
    assert len(exc.value.dicas) == 1 and comando in exc.value.dicas[0]
    assert comando in str(exc.value)


def test_falha_de_rede_consulta_a_dps_e_recupera_a_nota_sem_reenviar(client, sefin):
    sefin.on('POST', '/nfse', NfseHttpError('tempo esgotado', method='POST', url='/nfse'))
    dps = dps_id(MUNICIPIO, EMITTER, 1, 42)
    sefin.on('GET', f'/dps/{dps[3:]}', SefinResponse(200, {'chaveAcesso': CHAVE}))
    enviado = {}

    def nota(_):
        return SefinResponse(200, {'chaveAcesso': CHAVE, 'nfseXmlGZipB64': gzip_b64(nfse_de(enviado['dps']))})

    original = sefin.post

    def capturar(path, payload):
        enviado['dps'] = ungzip(payload['dpsXmlGZipB64'])
        return original(path, payload)

    sefin.post = capturar
    sefin.on('GET', f'/nfse/{CHAVE}', nota)
    sefin.on('GET', f'/nfse/{CHAVE}/eventos/101101/1', SefinResponse(404, {}))

    result = client.emissao.emit(client.emissao.prepare(pedido()), confirm=True)
    assert result.chave_acesso == CHAVE
    assert len(sefin.posts()) == 1


def test_falha_de_rede_sem_nota_na_sefin_fica_incerta_e_nao_reenvia(client, sefin):
    sefin.on('POST', '/nfse', NfseHttpError('conexão interrompida', method='POST', url='/nfse'))
    dps = dps_id(MUNICIPIO, EMITTER, 1, 42)
    sefin.on('GET', f'/dps/{dps[3:]}', SefinResponse(404, {}))
    with pytest.raises(EmissionUncertainError) as exc:
        client.emissao.emit(client.emissao.prepare(pedido()), confirm=True)
    assert exc.value.draft_id == dps
    assert len(sefin.posts()) == 1


def test_nota_devolvida_com_outro_tomador_fica_incerta(client, sefin):
    responder_emissao(sefin, tomador='11222333000181')
    with pytest.raises(EmissionUncertainError, match='não conferem'):
        client.emissao.emit(client.emissao.prepare(pedido()), confirm=True)


def test_certificado_de_outro_cnpj_e_recusado_antes_de_qualquer_envio(sefin, numerador):
    outro = CertificadoA1.carregar(gerar_pfx('11444777000161', **VALIDADE), SENHA.decode())
    client = SefinClient(sefin, outro, EMITENTE, numerador=numerador, ambiente=2, relogio=lambda: QUANDO)
    with pytest.raises(CertificateError, match='CNPJ'):
        client.emissao.prepare(pedido())
    assert sefin.calls == []


def test_consulta_nota_por_chave(client, sefin, certificado):
    from nfseai.assinatura import assinar
    from nfseai.dps import build_dps

    dps = assinar(build_dps(pedido(), emitente=EMITENTE, serie=1, numero=7, ambiente=2, emitido_em=QUANDO),
                  certificado)
    sefin.on('GET', f'/nfse/{CHAVE}', SefinResponse(200, {'nfseXmlGZipB64': gzip_b64(nfse_de(dps))}))
    sefin.on('GET', f'/nfse/{CHAVE}/eventos/101101/1', SefinResponse(200, {'eventos': ['...']}))
    nota = client.notas.get(CHAVE)
    assert nota.chave_acesso == CHAVE
    assert nota.numero_dps == '7'
    assert nota.cancelada is True
    assert nota.situacao == 'NFS-e cancelada'


def test_chave_invalida_e_recusada_sem_consultar(client, sefin):
    with pytest.raises(NfseValidationError):
        client.notas.get('123')
    assert sefin.calls == []


def preparar_nota_ativa(sefin, certificado, cancelada=False):
    from nfseai.assinatura import assinar
    from nfseai.dps import build_dps

    dps = assinar(build_dps(pedido(), emitente=EMITENTE, serie=1, numero=7, ambiente=2, emitido_em=QUANDO),
                  certificado)
    sefin.on('GET', f'/nfse/{CHAVE}', SefinResponse(200, {'nfseXmlGZipB64': gzip_b64(nfse_de(dps))}))
    estado = {'cancelada': cancelada}
    sefin.on('GET', f'/nfse/{CHAVE}/eventos/101101/1',
             lambda _: SefinResponse(200, {'eventos': ['x']}) if estado['cancelada'] else SefinResponse(404, {}))
    return estado


def test_previa_de_cancelamento_nao_envia_nada(client, sefin, certificado):
    preparar_nota_ativa(sefin, certificado)
    previa = client.cancelamento.preview(CHAVE)
    assert previa.cancelamento_direto is True
    assert previa.motivos == {'1': 'Erro na emissão', '2': 'Serviço não prestado', '9': 'Outros'}
    assert sefin.posts() == []


def test_cancelamento_envia_pedido_assinado_e_confere_o_evento(client, sefin, certificado):
    estado = preparar_nota_ativa(sefin, certificado)

    def receber(payload):
        estado['cancelada'] = True
        return SefinResponse(201, {'eventoXmlGZipB64': gzip_b64(b'<evento/>')})

    sefin.on('POST', f'/nfse/{CHAVE}/eventos', receber)
    justificativa = 'Serviço não prestado: pedido cancelado pelo cliente.'
    with pytest.raises(ConfirmationRequired):
        client.cancelamento.cancel(CHAVE, motivo='2', justificativa=justificativa)
    result = client.cancelamento.cancel(CHAVE, motivo='2', justificativa=justificativa, confirm=True)

    [(_, _, payload)] = sefin.posts()
    pedido_xml = ungzip(payload['pedidoRegistroEventoXmlGZipB64'])
    validar_pedido_evento(pedido_xml)
    raiz = etree.fromstring(pedido_xml)
    assert raiz.find('n:infPedReg', N).get('Id') == f'PRE{CHAVE}101101'
    assert raiz.findtext('n:infPedReg/n:e101101/n:cMotivo', namespaces=N) == '2'
    assert b'<SignatureValue>' in pedido_xml
    assert result.sucesso is True
    assert result.situacao_apos == 'NFS-e cancelada'


def test_cancelamento_recusa_justificativa_curta(client, sefin):
    with pytest.raises(NfseValidationError, match='15 a 255'):
        client.cancelamento.cancel(CHAVE, motivo='2', justificativa='curta', confirm=True)
    assert sefin.calls == []


def test_tributos_aproximados_entram_no_resumo(client):
    prepared = client.emissao.prepare(pedido())
    assert prepared.resumo['Tributação']['Tributos aproximados (%)'] == (
        f'federal {APROXIMADOS.federal}, estadual {APROXIMADOS.estadual}, municipal {APROXIMADOS.municipal}'
    )


def test_contexto_tls_nao_deixa_a_chave_em_disco(certificado, tmp_path):
    import ssl

    contexto = certificado.contexto_tls(tmp_path)
    assert isinstance(contexto, ssl.SSLContext)
    assert list(tmp_path.iterdir()) == []


def test_sessao_real_usa_tls_mutuo_sem_deixar_arquivo(certificado, tmp_path):
    from nfseai.sefin import SefinSession

    session = SefinSession(certificado, 'producao-restrita', pasta_privada=tmp_path)
    try:
        assert session.base_url == 'https://sefin.producaorestrita.nfse.gov.br/SefinNacional'
        assert session.tipo_ambiente == 2
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def sessao_simulada(handler, tmp_path):
    import httpx

    from nfseai.sefin import SefinSession

    http = httpx.Client(base_url='https://sefin.teste', transport=httpx.MockTransport(handler))
    return SefinSession(None, 'producao-restrita', pasta_privada=tmp_path, http_client=http, min_interval=0)


def test_sessao_envia_json_e_devolve_status_e_corpo(tmp_path):
    import json

    recebido = {}

    def handler(request):
        recebido['metodo'], recebido['caminho'] = request.method, request.url.path
        recebido['corpo'] = json.loads(request.content)
        return __import__('httpx').Response(400, json={'erros': [{'codigo': 'E0014', 'descricao': 'DPS repetida'}]})

    response = sessao_simulada(handler, tmp_path).post('/nfse', {'dpsXmlGZipB64': 'abc'})
    assert recebido == {'metodo': 'POST', 'caminho': '/nfse', 'corpo': {'dpsXmlGZipB64': 'abc'}}
    assert response.status_code == 400
    assert response.data['erros'][0]['codigo'] == 'E0014'


def test_sessao_transforma_falha_de_rede_em_nfse_http_error(tmp_path):
    import httpx

    def handler(request):
        raise httpx.ConnectTimeout('tempo esgotado', request=request)

    with pytest.raises(NfseHttpError, match='falha de rede'):
        sessao_simulada(handler, tmp_path).get('/nfse/123')


def test_sessao_aceita_resposta_que_nao_e_json(tmp_path):
    import httpx

    response = sessao_simulada(lambda request: httpx.Response(502, text='<html>Bad Gateway</html>'), tmp_path).get('/x')
    assert response.status_code == 502
    assert 'Bad Gateway' in response.data['texto']


def test_operations_do_projeto_funcionam_com_o_cliente_da_sefin(client, sefin, tmp_path):
    from nfseai import Ledger
    from nfseai.errors import DuplicateReferenceError
    from nfseai.operations import emit

    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    responder_emissao(sefin)

    ensaio = emit(client, ledger, 'empresa', pedido())
    assert ensaio['ensaio'] is True and ensaio['emitida'] is False
    assert sefin.posts() == []

    emitida = emit(client, ledger, 'empresa', pedido(), confirm=True)
    assert emitida['emitida'] is True
    assert emitida['chave_acesso'] == CHAVE
    assert (emitida['serie_dps'], emitida['numero_dps']) == ('00001', '42')
    assert ledger.get('empresa', 'pedido-1042')['status'] == 'emitida'

    with pytest.raises(DuplicateReferenceError):
        emit(client, ledger, 'empresa', pedido(), confirm=True)
    assert len(sefin.posts()) == 1


def test_erro_de_evento_vem_em_um_objeto_so():
    """No Swagger, a resposta de erro dos eventos (ResponseErro) traz `erro`, um objeto, e não a lista `erros`."""
    from nfseai.sefin import _erros

    assert _erros({'erro': {'codigo': 'E0840', 'descricao': 'Evento já registrado'}}) == ['E0840: Evento já registrado']
    assert _erros({'erros': [{'Codigo': 'E0014', 'Descricao': 'DPS duplicada'}]}) == ['E0014: DPS duplicada']


def test_consulta_da_dps_usa_os_42_numeros_sem_o_prefixo(client, sefin):
    """Conferido na produção restrita: com o prefixo 'DPS' a Sefin responde 400 (E2400, 'deve conter 42 números')."""
    dps = dps_id(MUNICIPIO, EMITTER, 1, 42)
    sefin.on('GET', f'/dps/{dps[3:]}', SefinResponse(200, {'chaveAcesso': CHAVE}))
    assert client.notas.chave_da_dps(dps) == CHAVE
    assert client.notas.chave_da_dps(dps[3:]) == CHAVE
