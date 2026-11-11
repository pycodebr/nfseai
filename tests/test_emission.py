import re
from datetime import date
from decimal import Decimal

import pytest

from conftest import APP, BASE, EMPTY_LIST, SERVICO, TRIBUTACAO, load, response
from nfseai import (
    ConfirmationRequired,
    EmissionRejectedError,
    EmissionRequest,
    EmissionUncertainError,
    EmissorNacionalClient,
    NfseValidationError,
    PortalChangedError,
    Servico,
    Tomador,
    Tributacao,
    UnsupportedScenario,
)
from nfseai.models import PreparedEmission

COMPETENCIA = date(2026, 11, 9)
EMIT_PATH = f'{APP}/DPS/NFSe/Index'


def make_request(documento, valor, servico=SERVICO, tributacao=TRIBUTACAO, competencia=COMPETENCIA):
    return EmissionRequest(
        competencia=competencia, tomador=Tomador(documento=documento), valor=Decimal(valor), servico=servico,
        tributacao=tributacao,
    )


def pj_request(valor='10.00'):
    return make_request('11.444.777/0001-61', valor)


def pf_request(valor='1047.51'):
    return make_request('529.982.247-25', valor)


def as_pairs(body):
    return [list(pair) for pair in body]


def pf_conferencia():
    html = load('conferencia.html').replace('11.444.777/0001-61', '529.982.247-25')
    return html.replace('R$ 10,00', 'R$ 1.047,51')


def prepared_pf(draft_id):
    return PreparedEmission(draft_id=draft_id, request=pf_request(), tomador_nome='TOMADOR EXEMPLO 1', resumo={})


def test_prepare_sends_exactly_what_the_browser_sent(session, captured, draft_id):
    client = EmissorNacionalClient(session)
    prepared = client.emissao.prepare(pj_request())

    assert as_pairs(session.last_body(f'{APP}/DPS/Pessoas')) == captured['pessoas_pj']
    assert as_pairs(session.last_body(f'{APP}/DPS/Servico')) == captured['servico']
    assert as_pairs(session.last_body(f'{APP}/DPS/Tributacao')) == captured['tributacao']
    assert prepared.draft_id == draft_id
    assert prepared.tomador_nome == 'TOMADORA EXEMPLO LTDA'
    assert prepared.valor_servico == Decimal('10.00')
    assert prepared.valor_liquido == Decimal('10.00')
    assert session.count('GET', EMIT_PATH) == 0


def test_prepare_for_cpf_without_address_matches_browser(session, captured):
    session.on('GET', f'{APP}/DPS/EmitirNFSe', pf_conferencia().replace('R$ 1.047,51', 'R$ 10,00'))
    client = EmissorNacionalClient(session)
    client.emissao.prepare(pf_request('10.00'))

    body = as_pairs(session.last_body(f'{APP}/DPS/Pessoas'))
    assert body == captured['pessoas_pf']
    assert 'Tomador.InformarEndereco' not in [name for name, _ in body]


def test_new_field_in_portal_form_stops_before_posting(session):
    changed = load('form-step1.html').replace('</form>', '<input name="CampoNovo" value="x"/></form>')
    session.on('GET', f'{APP}/DPS/Pessoas', changed)
    with pytest.raises(PortalChangedError, match='CampoNovo'):
        EmissorNacionalClient(session).emissao.prepare(pj_request())
    assert session.count('POST', f'{APP}/DPS/Pessoas') == 0


def test_removed_field_in_portal_form_stops_before_posting(session):
    changed = re.sub(r'<input[^>]*name="Valores\.ValorServico"[^>]*>', '', load('form-step3.html'))
    session.on('GET', f'{APP}/DPS/Tributacao', changed)
    with pytest.raises(PortalChangedError, match='Valores.ValorServico'):
        EmissorNacionalClient(session).emissao.prepare(pj_request())
    assert session.count('POST', f'{APP}/DPS/Tributacao') == 0
    assert session.count('POST', f'{APP}/Rascunhos/Excluir/') == 1


def test_portal_validation_error_is_reported(session):
    page = '<span class="field-validation-error">O CPF/CNPJ informado é inválido.</span>'
    session.on('POST', f'{APP}/DPS/Pessoas', lambda **_: response(page))
    with pytest.raises(NfseValidationError, match='inválido'):
        EmissorNacionalClient(session).emissao.prepare(pj_request())


def test_conference_mismatch_discards_draft(session):
    with pytest.raises(NfseValidationError, match='valor do serviço diferente'):
        EmissorNacionalClient(session).emissao.prepare(pj_request('99.90'))
    assert session.count('POST', f'{APP}/Rascunhos/Excluir/') == 1


@pytest.mark.parametrize('tributacao', [
    Tributacao(aliquota_sn=Decimal('3'), reter_issqn=True),
    Tributacao(aliquota_sn=Decimal('3'), preencher_ibs_cbs=True),
    Tributacao(aliquota_sn=Decimal('3'), regime_apuracao_sn=2),
    Tributacao(tipo_valor_tributos=1),
])
def test_unmapped_scenarios_are_refused_before_any_request(session, tributacao):
    request = make_request('529.982.247-25', '10', tributacao=tributacao)
    with pytest.raises(UnsupportedScenario):
        EmissorNacionalClient(session).emissao.prepare(request)
    assert session.calls == []


@pytest.mark.parametrize(('servico', 'tributacao', 'match'), [
    (SERVICO, Tributacao(tipo_valor_tributos=4), 'alíquota'),
    (SERVICO, Tributacao(aliquota_sn=Decimal('100')), 'alíquota'),
    (Servico('080201', '122051900', 'Serviço'), TRIBUTACAO, 'tributação nacional'),
    (Servico('08.02.01', '1220519', 'Serviço'), TRIBUTACAO, 'NBS'),
    (Servico('08.02.01', '122051900', '  '), TRIBUTACAO, 'descrição'),
])
def test_incomplete_fiscal_data_is_refused_before_any_request(session, servico, tributacao, match):
    request = make_request('529.982.247-25', '10', servico=servico, tributacao=tributacao)
    with pytest.raises(NfseValidationError, match=match):
        EmissorNacionalClient(session).emissao.prepare(request)
    assert session.calls == []


def test_tax_estimate_can_be_omitted(session, captured):
    request = make_request('11.444.777/0001-61', '10.00', tributacao=Tributacao(tipo_valor_tributos=3))
    EmissorNacionalClient(session).emissao.prepare(request)
    body = dict(as_pairs(session.last_body(f'{APP}/DPS/Tributacao')))
    assert body['ValorTributos.TipoValorTributos'] == '3'
    assert body['ValorTributos.AliquotaSN'] == ''


def test_invalid_input_is_refused_before_any_request(session):
    client = EmissorNacionalClient(session)
    with pytest.raises(NfseValidationError):
        client.emissao.prepare(make_request('111.111.111-11', '10'))
    with pytest.raises(NfseValidationError):
        client.emissao.prepare(make_request('529.982.247-25', '0'))
    with pytest.raises(NfseValidationError):
        client.emissao.prepare(make_request('529.982.247-25', '10', competencia=date(2999, 1, 1)))
    assert session.calls == []


def test_unknown_nbs_is_refused(session):
    servico = Servico(codigo_tributacao_nacional='08.02.01', codigo_nbs='999999999', descricao='Serviço de exemplo.')
    request = make_request('11.444.777/0001-61', '10', servico=servico)
    with pytest.raises(NfseValidationError, match='NBS'):
        EmissorNacionalClient(session).emissao.prepare(request)
    assert session.count('POST', f'{APP}/DPS/Servico') == 0


def test_emit_requires_confirmation(session, draft_id):
    with pytest.raises(ConfirmationRequired):
        EmissorNacionalClient(session).emissao.emit(prepared_pf(draft_id))
    assert session.calls == []


def test_emit_reads_back_the_generated_note(session, draft_id):
    nota_html = load('nota.html')
    chave = re.search(r'\d{50}', nota_html).group(0)
    session.on('GET', f'{APP}/DPS/EmitirNFSe', pf_conferencia())
    session.on('GET', EMIT_PATH, lambda **_: response(
        '<html>NFS-e gerada</html>', url=f'{BASE}{APP}/DPS/NFSe/Concluida?chaveAcesso={chave}'
    ))
    session.on('GET', f'{APP}/Notas/Visualizar/Index', nota_html)

    result = EmissorNacionalClient(session).emissao.emit(prepared_pf(draft_id), confirm=True)

    assert result.chave_acesso == chave
    assert result.nota.valor_servico == Decimal('1047.51')
    assert session.alive_checks == 1
    assert session.count('GET', EMIT_PATH) == 1
    emit_call = next(kwargs for method, path, kwargs in session.calls if path == EMIT_PATH)
    assert emit_call['params'] == {'idr': draft_id}
    assert emit_call['retry_auth'] is False


def test_emit_finds_key_in_result_page_link(session, draft_id):
    nota_html = load('nota.html')
    chave = re.search(r'\d{50}', nota_html).group(0)
    session.on('GET', f'{APP}/DPS/EmitirNFSe', pf_conferencia())
    session.on('GET', EMIT_PATH, f'<a href="{APP}/Notas/Visualizar/Index/{chave}">Visualizar</a>')
    session.on('GET', f'{APP}/Notas/Visualizar/Index', nota_html)
    assert EmissorNacionalClient(session).emissao.emit(prepared_pf(draft_id), confirm=True).chave_acesso == chave


def test_emit_without_key_is_uncertain_and_never_retried(session, draft_id, tmp_path):
    session.on('GET', f'{APP}/DPS/EmitirNFSe', pf_conferencia())
    session.on('GET', EMIT_PATH, '<html><body>Processando</body></html>')
    client = EmissorNacionalClient(session, debug_dir=tmp_path)
    with pytest.raises(EmissionUncertainError) as error:
        client.emissao.emit(prepared_pf(draft_id), confirm=True)
    assert session.count('GET', EMIT_PATH) == 1
    assert error.value.draft_id == draft_id
    assert list(tmp_path.glob('emissao-*.html'))


def test_emit_rejected_by_portal(session, draft_id):
    session.on('GET', f'{APP}/DPS/EmitirNFSe', pf_conferencia())
    session.on('GET', EMIT_PATH, '<div class="alert-danger">E0014 - DPS já recepcionada.</div>')
    with pytest.raises(EmissionRejectedError, match='E0014'):
        EmissorNacionalClient(session).emissao.emit(prepared_pf(draft_id), confirm=True)
    assert session.count('GET', EMIT_PATH) == 1


def test_emit_blocks_possible_duplicate(session, draft_id):
    session.on('GET', f'{APP}/Notas/Emitidas', lambda params=None, **_: response(
        load('emitidas.html') if (params or {}).get('pg') is None else EMPTY_LIST
    ))
    with pytest.raises(NfseValidationError, match='mesmo tomador e valor'):
        EmissorNacionalClient(session).emissao.emit(prepared_pf(draft_id), confirm=True)
    assert session.count('GET', EMIT_PATH) == 0


def test_emit_stops_if_draft_changed_after_prepare(session, draft_id):
    session.on('GET', f'{APP}/DPS/EmitirNFSe', pf_conferencia().replace('R$ 1.047,51', 'R$ 2.000,00'))
    with pytest.raises(NfseValidationError, match='valor do serviço diferente'):
        EmissorNacionalClient(session).emissao.emit(prepared_pf(draft_id), confirm=True)
    assert session.count('GET', EMIT_PATH) == 0
