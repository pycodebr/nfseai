"""Linha de comando e assistente no caminho A (empresa conectada por certificado digital)."""
import datetime as dt
import json

import pytest

from certificado_teste import SENHA, gerar_pfx
from nfseai import Store
from nfseai.cli import main
from nfseai.sefin import SefinClient, SefinResponse, gzip_b64
from nfseai.wizard import Console, WizardAborted, connect_certificate
from test_sefin import CHAVE, QUANDO, FakeSefin, responder_emissao

EMITTER = '11222333000181'
SENHA_TXT = SENHA.decode()
VALIDADE = {'inicio': dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), 'dias_validade': 3650}
FISCAL = [
    '--codigo-servico', '080201', '--nbs', '122051900', '--descricao', 'Valor referente aos treinamentos prestados.',
    '--tributos-federais', '11,30', '--tributos-estaduais', '0', '--tributos-municipais', '5',
]
TOMADOR = [
    '--documento', '11.444.777/0001-61', '--nome', 'TOMADORA EXEMPLO LTDA', '--valor', '1.500,00',
    '--competencia', '20/11/2026', '--referencia', 'pedido-1042',
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    for key in ('NFSEAI_USER', 'NFSEAI_PASSWORD', 'NFSEAI_EMPRESA', 'NFSEAI_ENV_FILE', 'NFSEAI_CERT',
                'NFSEAI_CERT_PASSWORD'):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / 'home'
    monkeypatch.setenv('NFSEAI_HOME', str(path))
    return path


@pytest.fixture
def pfx_file(tmp_path):
    path = tmp_path / 'empresa.pfx'
    path.write_bytes(gerar_pfx(EMITTER, **VALIDADE))
    return path


@pytest.fixture
def conectada(home, pfx_file):
    store = Store()
    store.save_certificate('empresa-a', pfx_file, SENHA_TXT, ambiente='producao-restrita',
                           codigo_municipio='4199999', inscricao_municipal='123456')
    return store


@pytest.fixture
def sefin(conectada, monkeypatch):
    """Troca a sessão real da Sefin pela falsa, mantendo certificado, emitente e numeração do perfil."""
    fake = FakeSefin()
    original = SefinClient.from_profile.__func__

    def from_profile(cls, empresa=None, **kwargs):
        client = original(cls, empresa, **kwargs)
        client.session.close()
        client.session = fake
        client.notas._session = fake
        client._relogio = lambda: QUANDO
        return client

    monkeypatch.setattr(SefinClient, 'from_profile', classmethod(from_profile))
    return fake


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    assert SENHA_TXT not in captured.out + captured.err
    data = json.loads(captured.out) if captured.out.strip() else None
    return code, data, captured.err


# ----------------------------------------------------------------------- assistente
def console(respostas, senha=SENHA_TXT):
    respostas = iter(respostas)
    saida = []
    return Console(input_fn=lambda _: next(respostas), getpass_fn=lambda _: senha,
                   out=type('Out', (), {'write': lambda self, t: saida.append(t), 'flush': lambda self: None})())


def test_assistente_grava_o_certificado_depois_de_mostrar_a_empresa(home, pfx_file):
    store = Store()
    resumo = connect_certificate(store, console(['s']), pfx_file, empresa='empresa-a', codigo_municipio='4199999')
    assert resumo['cnpj'] == EMITTER
    assert resumo['ambiente'] == 'producao-restrita'
    assert store.transporte('empresa-a') == 'sefin'


def test_assistente_pergunta_o_municipio_quando_nao_informado(home, pfx_file):
    store = Store()
    connect_certificate(store, console(['4199999', 's']), pfx_file, empresa='empresa-a')
    assert store.sefin_config('empresa-a')['codigo_municipio'] == '4199999'


def test_assistente_nao_grava_se_o_usuario_nao_confirmar_a_empresa(home, pfx_file):
    store = Store()
    with pytest.raises(WizardAborted):
        connect_certificate(store, console(['n']), pfx_file, empresa='empresa-a', codigo_municipio='4199999')
    assert not store.certificate_path('empresa-a').exists()


def test_assistente_com_senha_errada_nao_grava(home, pfx_file):
    store = Store()
    with pytest.raises(WizardAborted, match='senha'):
        connect_certificate(store, console(['s'], senha='errada-123'), pfx_file, empresa='empresa-a',
                            codigo_municipio='4199999')
    assert not store.certificate_path('empresa-a').exists()


def test_conectar_certificado_recusa_rodar_sem_terminal(home, pfx_file, capsys):
    code, _, err = run(capsys, 'conectar', '--certificado', str(pfx_file), '--empresa', 'empresa-a')
    assert code == 2
    assert 'nunca no chat' in err or 'terminal' in err


# ----------------------------------------------------------------------- status e configuração
def test_status_mostra_o_caminho_a_e_o_que_falta(conectada, capsys):
    code, data, _ = run(capsys, 'status', '--empresa', 'empresa-a')
    assert code == 4
    assert data['conectada'] is True
    assert data['caminho'] == 'sefin'
    assert data['certificado']['cnpj'] == EMITTER
    assert data['ambiente'] == 'producao-restrita'
    assert data['leiaute'] == '1.01-nt009'
    assert data['perfil_fiscal']['faltando'] == ['codigo_tributacao_nacional', 'descricao', 'tributos_aproximados']


def test_configurar_grava_os_tributos_aproximados(conectada, capsys):
    code, data, _ = run(capsys, 'configurar', '--empresa', 'empresa-a', *FISCAL)
    assert code == 0
    assert data['perfil_fiscal']['completo'] is True
    assert data['perfil_fiscal']['tributos_federais'] == '11.30'
    code, data, _ = run(capsys, 'status', '--empresa', 'empresa-a')
    assert code == 0


def test_configurar_troca_a_serie_e_o_leiaute(conectada, capsys):
    code, data, _ = run(capsys, 'configurar', '--empresa', 'empresa-a', '--serie', '100', '--leiaute', '1.01')
    assert code == 0
    assert data['caminho_a'] == {'serie': 100, 'leiaute': '1.01'}
    _, data, _ = run(capsys, 'status', '--empresa', 'empresa-a')
    assert data['emitente']['serie'] == 100 and data['leiaute'] == '1.01'

    code, data, _ = run(capsys, 'configurar', '--empresa', 'empresa-a', '--leiaute', 'padrao', *FISCAL)
    assert code == 0
    assert data['caminho_a'] == {'serie': 100, 'leiaute': '1.01-nt009'}
    assert data['perfil_fiscal']['completo'] is True


def test_configurar_recusa_serie_fora_da_faixa(conectada, capsys):
    code, _, err = run(capsys, 'configurar', '--empresa', 'empresa-a', '--serie', '50000')
    assert code != 0 and '49999' in err
    _, data, _ = run(capsys, 'status', '--empresa', 'empresa-a')
    assert data['emitente']['serie'] == 1


def test_serie_e_leiaute_nao_existem_no_portal(conectada, capsys):
    conectada.save_credentials('empresa-b', '11444777000161', 'senha-portal')
    code, _, err = run(capsys, 'configurar', '--empresa', 'empresa-b', '--serie', '2')
    assert code != 0 and 'caminho A' in err


def test_emissao_usa_a_serie_nova(sefin, capsys):
    run(capsys, 'configurar', '--empresa', 'empresa-a', *FISCAL)
    run(capsys, 'configurar', '--empresa', 'empresa-a', '--serie', '100')
    responder_emissao(sefin)
    code, data, _ = run(capsys, 'emitir', '--empresa', 'empresa-a', *TOMADOR, '--confirm')
    assert code == 0
    assert data['numero_dps'] == '1'
    assert data['serie_dps'].lstrip('0') == '100'


def test_empresas_mostra_o_caminho_de_cada_uma(conectada, capsys):
    conectada.save_credentials('empresa-b', '11444777000161', 'senha-portal')
    _, data, _ = run(capsys, 'empresas')
    caminhos = {item['nome']: (item['caminho'], item['conectada']) for item in data['empresas']}
    assert caminhos == {'empresa-a': ('sefin', True), 'empresa-b': ('portal', True)}


# ----------------------------------------------------------------------- emissão
def test_emitir_pelo_caminho_a_faz_ensaio_e_depois_emite(sefin, capsys):
    run(capsys, 'configurar', '--empresa', 'empresa-a', *FISCAL)
    responder_emissao(sefin)

    code, data, _ = run(capsys, 'emitir', '--empresa', 'empresa-a', *TOMADOR)
    assert code == 0
    assert data['ensaio'] is True and sefin.posts() == []

    code, data, _ = run(capsys, 'emitir', '--empresa', 'empresa-a', *TOMADOR, '--confirm')
    assert code == 0
    assert data['emitida'] is True and data['chave_acesso'] == CHAVE
    assert data['numero_dps'] == '1'
    assert len(sefin.posts()) == 1


def test_reter_iss_exige_endereco_do_tomador(sefin, capsys):
    run(capsys, 'configurar', '--empresa', 'empresa-a', *FISCAL)
    code, _, err = run(capsys, 'preparar', '--empresa', 'empresa-a', *TOMADOR, '--reter-iss')
    assert code == 1
    assert 'endereço do tomador' in err
    code, data, _ = run(
        capsys, 'preparar', '--empresa', 'empresa-a', *TOMADOR, '--reter-iss', '--tomador-cep', '80010-000',
        '--tomador-municipio', '4199999', '--tomador-logradouro', 'RUA EXEMPLO', '--tomador-numero', '100',
        '--tomador-bairro', 'CENTRO',
    )
    assert code == 0
    assert data['resumo']['Tributação']['ISSQN retido pelo tomador'] == 'sim'


def test_comandos_so_do_portal_recusam_no_caminho_a(sefin, capsys):
    for argv in (['emitidas'], ['rascunhos'], ['whoami'], ['servicos', 'treinamento'], ['pdf', CHAVE]):
        code, _, err = run(capsys, *argv, '--empresa', 'empresa-a')
        assert code == 2, argv
        assert 'caminho B' in err


def test_xml_oficial_da_nota(sefin, capsys, tmp_path):
    from nfseai.assinatura import assinar
    from nfseai.dps import build_dps
    from test_dps import EMITENTE, pedido
    from test_sefin import nfse_de

    certificado = Store().load_certificate('empresa-a')
    dps = assinar(build_dps(pedido(), emitente=EMITENTE, serie=1, numero=7, ambiente=2, emitido_em=QUANDO),
                  certificado)
    sefin.on('GET', f'/nfse/{CHAVE}', SefinResponse(200, {'nfseXmlGZipB64': gzip_b64(nfse_de(dps))}))
    sefin.on('GET', f'/nfse/{CHAVE}/eventos/101101/1', SefinResponse(404, {}))
    destino = tmp_path / 'nota.xml'
    code, data, _ = run(capsys, 'xml', CHAVE, '--out', str(destino), '--empresa', 'empresa-a')
    assert code == 0
    assert destino.read_bytes().startswith(b'<NFSe')
    assert destino.stat().st_mode & 0o777 == 0o600
    assert data['arquivo'] == str(destino)


def test_consulta_da_dps(sefin, capsys):
    sefin.on('GET', '/dps/123', SefinResponse(200, {'chaveAcesso': CHAVE}))
    code, data, _ = run(capsys, 'dps', 'DPS123', '--empresa', 'empresa-a')
    assert code == 0
    assert data == {'id_dps': 'DPS123', 'encontrada': True, 'chave_acesso': CHAVE}


def test_emitir_com_irrf_retido_pelo_percentual_do_perfil(sefin, capsys):
    run(capsys, 'configurar', '--empresa', 'empresa-a', *FISCAL, '--aliquota-irrf', '1,5')
    responder_emissao(sefin)
    argv = ['--documento', '11.444.777/0001-61', '--nome', 'TOMADORA EXEMPLO LTDA', '--valor', '1.234,50',
            '--competencia', '20/11/2026', '--referencia', 'pedido-77']
    code, data, _ = run(capsys, 'emitir', '--empresa', 'empresa-a', *argv)
    assert code == 0
    assert data['resumo']['Tributação']['IRRF retido'] == '18.51'
    assert data['resumo']['Valores']['Valor líquido'] == '1215.99'

    code, data, _ = run(capsys, 'emitir', '--empresa', 'empresa-a', *argv, '--sem-irrf')
    assert data['resumo']['Tributação']['IRRF retido'] == 'não'
