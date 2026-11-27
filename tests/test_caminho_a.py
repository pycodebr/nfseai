"""Caminho A na máquina do usuário: certificado e emitente por empresa, numeração da DPS e escolha do cliente."""
import datetime as dt
import threading
from decimal import Decimal

import pytest

from certificado_teste import SENHA, gerar_pfx
from nfseai import EmissorNacionalClient, Ledger, NotConnectedError, ProfileError, Store
from nfseai.errors import CertificateError
from nfseai.models import TributosAproximados
from nfseai.sefin import SefinClient

EMITTER = '11222333000181'
SENHA_TXT = SENHA.decode()
VALIDADE = {'inicio': dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), 'dias_validade': 3650}


@pytest.fixture
def store(tmp_path, monkeypatch):
    for key in ('NFSEAI_USER', 'NFSEAI_PASSWORD', 'NFSEAI_EMPRESA', 'NFSEAI_ENV_FILE', 'NFSEAI_HOME',
                'NFSEAI_CERT', 'NFSEAI_CERT_PASSWORD'):
        monkeypatch.delenv(key, raising=False)
    return Store(home=tmp_path / 'home')


@pytest.fixture
def pfx():
    return gerar_pfx(EMITTER, **VALIDADE)


def conectar(store, pfx, nome='empresa-a'):
    return store.save_certificate(
        nome, pfx, SENHA_TXT, ambiente='producao-restrita', codigo_municipio='4199999',
        inscricao_municipal='123456',
    )


# ----------------------------------------------------------------------- numeração
def test_numeracao_da_dps_comeca_em_1_e_nunca_repete(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    assert ledger.reservar_dps('empresa-a', 1, 'pedido-1') == 1
    assert ledger.reservar_dps('empresa-a', 1, 'pedido-2') == 2
    assert ledger.reservar_dps('empresa-b', 1, 'pedido-1') == 1
    assert ledger.reservar_dps('empresa-a', 2, 'pedido-3') == 1
    assert [r['referencia'] for r in ledger.reservas_dps('empresa-a')] == ['pedido-1', 'pedido-2', 'pedido-3']


def test_numeracao_reservada_em_paralelo_nao_repete_numero(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    numeros = []

    def reservar(i):
        numeros.append(ledger.reservar_dps('empresa-a', 1, f'pedido-{i}'))

    threads = [threading.Thread(target=reservar, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(numeros) == list(range(1, 21))


# ----------------------------------------------------------------------- certificado
def test_certificado_fica_na_pasta_privada_e_a_senha_nao_vai_para_o_config(store, pfx):
    resumo = conectar(store, pfx)
    assert resumo['cnpj'] == EMITTER
    arquivo = store.certificate_path('empresa-a')
    assert arquivo.read_bytes() == pfx
    assert arquivo.stat().st_mode & 0o777 == 0o600
    assert arquivo.parent.stat().st_mode & 0o777 == 0o700
    assert SENHA_TXT not in store.config_path.read_text()
    assert SENHA_TXT not in str(resumo)
    assert store.transporte('empresa-a') == 'sefin'
    assert store.certificate_origin('empresa-a') == 'arquivo'
    assert store.load_certificate('empresa-a').cnpj == EMITTER


def test_senha_errada_nao_grava_nada(store, pfx):
    with pytest.raises(CertificateError):
        store.save_certificate('empresa-a', pfx, 'errada-123', ambiente='producao-restrita',
                               codigo_municipio='4199999')
    assert not store.certificate_path('empresa-a').exists()
    assert store.profiles() == []


def test_certificado_vencido_nao_e_gravado(store):
    vencido = gerar_pfx(EMITTER, inicio=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc), dias_validade=30)
    with pytest.raises(CertificateError, match='venc'):
        conectar(store, vencido)
    assert not store.certificate_path('empresa-a').exists()


def test_emitente_do_caminho_a_vem_do_certificado_e_da_configuracao(store, pfx):
    conectar(store, pfx)
    emitente = store.emitente('empresa-a')
    assert (emitente.cnpj, emitente.codigo_municipio, emitente.inscricao_municipal) == (EMITTER, '4199999', '123456')
    assert store.sefin_config('empresa-a')['ambiente'] == 'producao-restrita'
    assert store.sefin_config('empresa-a')['serie'] == 1


def test_ambiente_e_municipio_sao_validados(store, pfx):
    with pytest.raises(ProfileError, match='ambiente'):
        store.save_certificate('empresa-a', pfx, SENHA_TXT, ambiente='teste', codigo_municipio='4199999')
    with pytest.raises(ProfileError, match='IBGE'):
        store.save_certificate('empresa-a', pfx, SENHA_TXT, ambiente='producao', codigo_municipio='41')


def test_certificado_pelo_ambiente_para_servidores(store, pfx, tmp_path, monkeypatch):
    conectar(store, pfx)
    arquivo = tmp_path / 'cofre.pfx'
    arquivo.write_bytes(pfx)
    store.certificate_path('empresa-a').unlink()
    monkeypatch.setenv('NFSEAI_EMPRESA_A_CERT', str(arquivo))
    monkeypatch.setenv('NFSEAI_EMPRESA_A_CERT_PASSWORD', SENHA_TXT)
    assert store.certificate_origin('empresa-a') == 'ambiente'
    assert store.load_certificate('empresa-a').cnpj == EMITTER


def test_sem_certificado_diz_ao_agente_o_que_fazer(store):
    with pytest.raises(NotConnectedError, match='conectar --certificado'):
        store.load_certificate('empresa-a')


def test_desconectar_apaga_certificado_e_senha(store, pfx):
    conectar(store, pfx)
    assert store.remove_credentials('empresa-a') is True
    assert not store.certificate_path('empresa-a').exists()
    assert store.certificate_origin('empresa-a') is None


# ----------------------------------------------------------------------- padrões fiscais
def test_padroes_fiscais_do_caminho_a(store, pfx):
    conectar(store, pfx)
    store.save_fiscal(
        'empresa-a', codigo_tributacao_nacional='08.02.01', codigo_nbs='122051900', descricao='Treinamento.',
        tributos_federais=Decimal('11.30'), tributos_estaduais=Decimal('0'), tributos_municipais=Decimal('5'),
    )
    fiscal = store.fiscal('empresa-a')
    assert fiscal.missing('sefin') == []
    assert fiscal.missing('portal') == ['aliquota_sn']
    tributacao = fiscal.tributacao('sefin')
    assert tributacao.tributos_aproximados == TributosAproximados(Decimal('11.30'), Decimal('0'), Decimal('5'))


def test_caminho_a_exige_tributos_aproximados(store, pfx):
    conectar(store, pfx)
    store.save_fiscal('empresa-a', codigo_tributacao_nacional='08.02.01', codigo_nbs='122051900',
                      descricao='Treinamento.')
    assert store.fiscal('empresa-a').missing('sefin') == ['tributos_aproximados']


# ----------------------------------------------------------------------- escolha do cliente
def test_from_profile_devolve_o_cliente_da_sefin_para_empresa_com_certificado(store, pfx):
    conectar(store, pfx)
    with EmissorNacionalClient.from_profile('empresa-a', home=store.home) as client:
        assert isinstance(client, SefinClient)
        assert client.empresa == 'empresa-a'
        assert client.tipo_ambiente == 2
        assert client.emitente.cnpj == EMITTER
        serie, numero = client.numerador(type('R', (), {'referencia': 'pedido-1'})())
        assert (serie, numero) == (1, 1)
    assert [p.name for p in (store.home / 'tls').iterdir()] == []


def test_empresa_do_portal_continua_no_caminho_b(store):
    store.save_credentials('empresa-b', '11444777000161', 'senha-portal')
    assert store.transporte('empresa-b') == 'portal'
    with EmissorNacionalClient.from_profile('empresa-b', home=store.home) as client:
        assert not isinstance(client, SefinClient)


def test_nbs_e_opcional_no_caminho_a(store, pfx):
    """No caminho A a Sefin aceita a DPS sem NBS (o grupo cNBS é opcional no XSD 1.01); o portal continua exigindo."""
    conectar(store, pfx)
    store.save_fiscal('empresa-a', codigo_tributacao_nacional='08.02.01', descricao='Treinamento em gestão.',
                      tributos_federais=Decimal('11.30'), tributos_estaduais=Decimal('0'),
                      tributos_municipais=Decimal('4.00'))
    fiscal = store.fiscal('empresa-a')
    assert fiscal.missing('sefin') == []
    assert 'codigo_nbs' in fiscal.missing('portal')
    assert fiscal.servico('sefin').codigo_nbs == ''


def test_irrf_calculado_pelo_percentual_e_arredondado_para_baixo(store, pfx):
    """Truncado nos centavos: R$ 1.234,50 x 1,5% = 18,5175 -> R$ 18,51."""
    conectar(store, pfx)
    store.save_fiscal('empresa-a', codigo_tributacao_nacional='08.02.01', descricao='Treinamento em gestão.',
                      tributos_federais=Decimal('11.30'), tributos_estaduais=Decimal('0'),
                      tributos_municipais=Decimal('4.00'), aliquota_irrf=Decimal('1.5'))
    fiscal = store.fiscal('empresa-a')
    assert fiscal.tributacao('sefin', valor=Decimal('1234.50')).retencao_irrf == Decimal('18.51')
    assert fiscal.tributacao('sefin', valor=Decimal('1234.50'), reter_irrf=False).retencao_irrf is None


def test_leiaute_da_empresa_segue_o_ambiente_e_pode_ser_forcado(store, pfx):
    from nfseai.dps import LEIAUTE_NT009, LEIAUTE_PUBLICADO
    from nfseai.sefin import SefinClient

    conectar(store, pfx)
    with SefinClient.from_profile('empresa-a', store=store) as client:
        assert client.leiaute == LEIAUTE_NT009
    store.save_sefin_options('empresa-a', leiaute=LEIAUTE_PUBLICADO)
    with SefinClient.from_profile('empresa-a', store=store) as client:
        assert client.leiaute == LEIAUTE_PUBLICADO


def test_irrf_ate_dez_reais_nao_e_retido(store, pfx):
    """Lei 9.430/1996, art. 67: dispensada a retenção de IR de valor igual ou inferior a R$ 10,00."""
    conectar(store, pfx)
    store.save_fiscal('empresa-a', codigo_tributacao_nacional='08.02.01', descricao='Treinamento em gestão.',
                      tributos_federais=Decimal('11.30'), tributos_estaduais=Decimal('0'),
                      tributos_municipais=Decimal('4.00'), aliquota_irrf=Decimal('1.5'))
    fiscal = store.fiscal('empresa-a')
    assert fiscal.tributacao('sefin', valor=Decimal('100.00')).retencao_irrf is None       # 1,50
    assert fiscal.tributacao('sefin', valor=Decimal('666.67')).retencao_irrf is None       # 10,00
    assert fiscal.tributacao('sefin', valor=Decimal('667.40')).retencao_irrf == Decimal('10.01')
