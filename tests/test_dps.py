from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from lxml import etree

from nfseai import Endereco, NfseValidationError, Servico, Tomador, Tributacao, UnsupportedScenario
from nfseai.dps import (
    AMBIENTE_HOMOLOGACAO,
    AMBIENTE_PRODUCAO,
    LEIAUTE_NT009,
    LEIAUTE_PUBLICADO,
    NS,
    Emitente,
    build_dps,
    dps_id,
    validar_dps,
)
from nfseai.models import EmissionRequest, TributosAproximados

EMITTER = '11222333000181'
TOMADOR = '11444777000161'
MUNICIPIO = '4199999'  # fictício, o mesmo do conftest
BRT = timezone(timedelta(hours=-3))

EMITENTE = Emitente(cnpj=EMITTER, codigo_municipio=MUNICIPIO, inscricao_municipal='123456')
ENDERECO = Endereco(
    cep='80010000', codigo_municipio=MUNICIPIO, nome_municipio='CIDADE EXEMPLO', bairro='CENTRO',
    logradouro='RUA EXEMPLO', numero='100',
)
SERVICO = Servico(
    codigo_tributacao_nacional='08.02.01',
    codigo_nbs='122051900',
    descricao='Valor referente aos treinamentos prestados.',
)

APROXIMADOS = TributosAproximados(federal=Decimal('11.30'), estadual=Decimal('0'), municipal=Decimal('5.00'))


def pedido(**tributacao):
    return EmissionRequest(
        competencia=date(2026, 11, 20),
        tomador=Tomador(documento=TOMADOR, nome='TOMADORA EXEMPLO LTDA', endereco=ENDERECO),
        valor=Decimal('1500.00'),
        servico=SERVICO,
        tributacao=Tributacao(**{'tributos_aproximados': APROXIMADOS, **tributacao}),
        referencia='pedido-1042',
    )


def montar(request=None, **kwargs):
    defaults = {
        'emitente': EMITENTE, 'serie': 1, 'numero': 42, 'ambiente': 2,
        'emitido_em': datetime(2026, 11, 20, 10, 30, tzinfo=BRT),
    }
    defaults.update(kwargs)
    return build_dps(request or pedido(), **defaults)


def campo(xml, caminho):
    return etree.fromstring(xml).findtext(caminho, namespaces={'n': NS})


def test_dps_id_concatena_municipio_tipo_inscricao_serie_e_numero():
    assert dps_id(MUNICIPIO, EMITTER, 1, 42) == f'DPS{MUNICIPIO}2{EMITTER}00001000000000000042'


def test_dps_id_de_cpf_completa_com_zeros_e_usa_tipo_1():
    assert dps_id(MUNICIPIO, '52998224725', 7, 1) == 'DPS' + MUNICIPIO + '1' + '00052998224725' + '00007' + '1'.zfill(15)


def test_dps_sem_retencao_valida_no_xsd_oficial():
    xml = montar()
    validar_dps(xml)
    assert etree.fromstring(xml).find('n:infDPS', {'n': NS}).get('Id') == dps_id(MUNICIPIO, EMITTER, 1, 42)
    assert campo(xml, 'n:infDPS/n:tpAmb') == '2'
    assert campo(xml, 'n:infDPS/n:serie') == '00001'
    assert campo(xml, 'n:infDPS/n:nDPS') == '42'
    assert campo(xml, 'n:infDPS/n:dhEmi') == '2026-11-20T10:30:00-03:00'
    assert campo(xml, 'n:infDPS/n:dCompet') == '2026-11-20'
    assert campo(xml, 'n:infDPS/n:prest/n:regTrib/n:opSimpNac') == '1'
    assert campo(xml, 'n:infDPS/n:toma/n:CNPJ') == TOMADOR
    assert campo(xml, 'n:infDPS/n:serv/n:cServ/n:cTribNac') == '080201'
    assert campo(xml, 'n:infDPS/n:valores/n:vServPrest/n:vServ') == '1500.00'
    assert campo(xml, 'n:infDPS/n:valores/n:trib/n:tribMun/n:tpRetISSQN') == '1'
    assert campo(xml, 'n:infDPS/n:valores/n:trib/n:tribMun/n:pAliq') is None
    assert campo(xml, 'n:infDPS/n:valores/n:trib/n:totTrib/n:pTotTrib/n:pTotTribFed') == '11.30'
    assert campo(xml, 'n:infDPS/n:valores/n:trib/n:totTrib/n:indTotTrib') is None
    assert campo(xml, 'n:infDPS/n:prest/n:xNome') is None


def test_aliquota_do_iss_vai_na_dps_somente_quando_informada():
    xml = montar(pedido(aliquota_iss=Decimal('5.00')))
    validar_dps(xml)
    assert campo(xml, 'n:infDPS/n:valores/n:trib/n:tribMun/n:pAliq') == '5.00'


def test_dps_com_issqn_retido_pelo_tomador():
    xml = montar(pedido(reter_issqn=True))
    validar_dps(xml)
    assert campo(xml, 'n:infDPS/n:valores/n:trib/n:tribMun/n:tpRetISSQN') == '2'
    assert campo(xml, 'n:infDPS/n:toma/n:end/n:endNac/n:cMun') == MUNICIPIO


def test_retencao_exige_endereco_do_tomador():
    request = pedido(reter_issqn=True)
    sem_endereco = EmissionRequest(
        competencia=request.competencia, tomador=Tomador(documento=TOMADOR, nome='TOMADORA EXEMPLO LTDA'),
        valor=request.valor, servico=request.servico, tributacao=request.tributacao,
    )
    with pytest.raises(NfseValidationError, match='endereço do tomador'):
        montar(sem_endereco)


def test_serie_fora_da_faixa_de_aplicativo_proprio_e_recusada():
    with pytest.raises(NfseValidationError, match='série'):
        montar(serie=70000)


def test_cenarios_ainda_nao_mapeados_sao_recusados():
    with pytest.raises(UnsupportedScenario):
        montar(pedido(preencher_ibs_cbs=True))
    with pytest.raises(UnsupportedScenario):
        montar(emitente=Emitente(cnpj=EMITTER, codigo_municipio=MUNICIPIO, opcao_simples=3))


def test_nao_optante_precisa_informar_o_total_aproximado_dos_tributos():
    with pytest.raises(NfseValidationError, match='tributos'):
        montar(pedido(tributos_aproximados=None))


def test_xsd_recusa_dps_adulterada():
    xml = montar().replace(b'<vServ>1500.00</vServ>', b'<vServ>1.500,00</vServ>')
    with pytest.raises(NfseValidationError, match='XSD'):
        validar_dps(xml)


def test_dps_sem_nbs_omite_o_grupo_e_valida():
    from dataclasses import replace

    request = pedido()
    xml = montar(replace(request, servico=replace(request.servico, codigo_nbs='')))
    validar_dps(xml)
    assert campo(xml, 'n:infDPS/n:serv/n:cServ/n:cNBS') is None


def test_dps_com_irrf_retido():
    xml = montar(pedido(retencao_irrf=Decimal('18.51')))
    validar_dps(xml)
    assert campo(xml, 'n:infDPS/n:valores/n:trib/n:tribFed/n:vRetIRRF') == '18.51'
    assert campo(xml, 'n:infDPS/n:valores/n:trib/n:tribFed/n:piscofins') is None


def test_dps_sem_retencao_federal_nao_tem_grupo_tribfed():
    assert campo(montar(), 'n:infDPS/n:valores/n:trib/n:tribFed') is None


def test_irrf_deve_ser_maior_que_zero_e_menor_que_o_servico():
    with pytest.raises(NfseValidationError, match='IRRF'):
        montar(pedido(retencao_irrf=Decimal('1500.00')))
    with pytest.raises(NfseValidationError, match='IRRF'):
        montar(pedido(retencao_irrf=Decimal('0')))


# ----------------------------------------------------------------------- leiaute da Nota Técnica 009
def filhos(xml, caminho='n:infDPS'):
    return [etree.QName(e).localname for e in etree.fromstring(xml).find(caminho, namespaces={'n': NS})]


def test_producao_restrita_usa_o_leiaute_da_nt009():
    """Desde 20/10/2026 a produção restrita valida a DPS no leiaute da NT 009 e recusa o anterior (E1235)."""
    xml = montar(ambiente=AMBIENTE_HOMOLOGACAO)
    validar_dps(xml)
    ordem = filhos(xml)
    assert ordem[ordem.index('tpEmit') + 1] == 'finNFSe'
    assert campo(xml, 'n:infDPS/n:finNFSe') == '0'
    assert ordem[ordem.index('toma') + 1] == 'indDest'
    assert campo(xml, 'n:infDPS/n:indDest') == '0'
    assert filhos(xml, 'n:infDPS/n:serv/n:cServ') == ['cTribNac', 'cNBS', 'xDescServ']


def test_producao_continua_no_leiaute_publicado_em_fevereiro():
    xml = montar(ambiente=AMBIENTE_PRODUCAO)
    validar_dps(xml)
    assert 'finNFSe' not in filhos(xml) and 'indDest' not in filhos(xml)
    assert filhos(xml, 'n:infDPS/n:serv/n:cServ') == ['cTribNac', 'xDescServ', 'cNBS']


def test_leiaute_pode_ser_forcado_para_a_virada_da_producao():
    xml = montar(ambiente=AMBIENTE_PRODUCAO, leiaute=LEIAUTE_NT009)
    validar_dps(xml)
    assert campo(xml, 'n:infDPS/n:finNFSe') == '0'
    with pytest.raises(ValueError, match='leiaute'):
        montar(leiaute='2099')


def test_cada_leiaute_recusa_a_dps_do_outro():
    antigo = montar(ambiente=AMBIENTE_PRODUCAO)
    with pytest.raises(NfseValidationError, match='finNFSe'):
        validar_dps(antigo, leiaute=LEIAUTE_NT009)
    novo = montar(ambiente=AMBIENTE_HOMOLOGACAO)
    with pytest.raises(NfseValidationError):
        validar_dps(novo, leiaute=LEIAUTE_PUBLICADO)
