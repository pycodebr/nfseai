import re
from datetime import date
from decimal import Decimal

import pytest

from conftest import APP, BASE, CANCEL_PATH, JUSTIFICATIVA, SERVICO, TRIBUTACAO, load, response
from nfseai import (
    DuplicateReferenceError,
    EmissionRejectedError,
    EmissionRequest,
    EmissionUncertainError,
    EmissorNacionalClient,
    Ledger,
    NfseError,
    NfseValidationError,
    Tomador,
    operations,
)

EMPRESA = 'minha-empresa'
EMIT_PATH = f'{APP}/DPS/NFSe/Index'
DRAFT_DELETE = f'{APP}/Rascunhos/Excluir/'


def request_pf(referencia='venda-1', valor='1047.51'):
    return EmissionRequest(
        competencia=date(2026, 11, 9), tomador=Tomador(documento='529.982.247-25'), valor=Decimal(valor),
        servico=SERVICO, tributacao=TRIBUTACAO, referencia=referencia,
    )


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / 'ledger.sqlite3')


@pytest.fixture
def emit_session(session):
    """Cenário em que a conferência bate com o pedido e a emissão devolve a nota de exemplo."""
    nota_html = load('nota.html')
    key = re.search(r'\d{50}', nota_html).group(0)
    conferencia = load('conferencia.html').replace('11.444.777/0001-61', '529.982.247-25')
    session.on('GET', f'{APP}/DPS/EmitirNFSe', conferencia.replace('R$ 10,00', 'R$ 1.047,51'))
    session.on('GET', EMIT_PATH, lambda **_: response(
        '<html>NFS-e gerada</html>', url=f'{BASE}{APP}/DPS/NFSe/Concluida?chaveAcesso={key}'
    ))
    session.on('GET', f'{APP}/Notas/Visualizar/Index', nota_html)
    session.key = key
    return session


def test_ledger_file_is_private(ledger, tmp_path):
    assert (tmp_path / 'ledger.sqlite3').stat().st_mode & 0o777 == 0o600


def test_rehearsal_discards_the_draft_and_leaves_no_record(emit_session, ledger):
    summary = operations.emit(EmissorNacionalClient(emit_session), ledger, EMPRESA, request_pf())
    assert summary['ensaio'] is True and summary['emitida'] is False
    assert summary['rascunho_descartado'] is True
    assert summary['tomador_nome'] == 'TOMADOR EXEMPLO 1'
    assert emit_session.count('GET', EMIT_PATH) == 0
    assert emit_session.count('POST', DRAFT_DELETE) == 1
    assert ledger.get(EMPRESA, 'venda-1') is None


def test_confirmed_emission_is_recorded_and_read_back(emit_session, ledger):
    summary = operations.emit(EmissorNacionalClient(emit_session), ledger, EMPRESA, request_pf(), confirm=True)
    assert summary['emitida'] is True
    assert summary['chave_acesso'] == emit_session.key
    assert summary['valor_servico'] == '1047.51'
    row = ledger.get(EMPRESA, 'venda-1')
    assert row['status'] == 'emitida'
    assert row['chave_acesso'] == emit_session.key
    assert row['numero_nfse'] == str(int(emit_session.key[23:36]))
    assert [event['evento'] for event in ledger.history(EMPRESA, 'venda-1')] == ['emitida']
    assert emit_session.count('GET', EMIT_PATH) == 1


def test_same_reference_is_never_emitted_twice(emit_session, ledger):
    client = EmissorNacionalClient(emit_session)
    operations.emit(client, ledger, EMPRESA, request_pf(), confirm=True)
    calls_before = len(emit_session.calls)
    for confirm in (False, True):
        with pytest.raises(DuplicateReferenceError, match='já foi emitida') as error:
            operations.emit(client, ledger, EMPRESA, request_pf(), confirm=confirm)
        assert error.value.chave_acesso == emit_session.key
    assert len(emit_session.calls) == calls_before
    assert emit_session.count('GET', EMIT_PATH) == 1


def test_same_reference_in_another_company_is_independent(emit_session, ledger):
    client = EmissorNacionalClient(emit_session)
    operations.emit(client, ledger, EMPRESA, request_pf(), confirm=True)
    summary = operations.emit(client, ledger, 'outra-empresa', request_pf())
    assert summary['ensaio'] is True


def test_uncertain_emission_blocks_the_reference(emit_session, ledger):
    emit_session.on('GET', EMIT_PATH, '<html><body>Processando</body></html>')
    client = EmissorNacionalClient(emit_session)
    with pytest.raises(EmissionUncertainError):
        operations.emit(client, ledger, EMPRESA, request_pf(), confirm=True)
    assert ledger.get(EMPRESA, 'venda-1')['status'] == 'incerta'
    with pytest.raises(DuplicateReferenceError, match='sem confirmação'):
        operations.emit(client, ledger, EMPRESA, request_pf(), confirm=True)
    assert emit_session.count('GET', EMIT_PATH) == 1


def test_interrupted_emission_blocks_the_reference(emit_session, ledger):
    ledger.start(empresa=EMPRESA, referencia='venda-1', tomador_documento='52998224725', valor=Decimal('10'),
                 competencia=date(2026, 11, 9), draft_id='DRAFT0001')
    with pytest.raises(DuplicateReferenceError, match='interrompida'):
        operations.emit(EmissorNacionalClient(emit_session), ledger, EMPRESA, request_pf(), confirm=True)
    assert emit_session.calls == []


def test_rejected_emission_can_be_tried_again(emit_session, ledger):
    emit_session.on('GET', EMIT_PATH, '<div class="alert-danger">E0014 - DPS já recepcionada.</div>')
    client = EmissorNacionalClient(emit_session)
    with pytest.raises(EmissionRejectedError):
        operations.emit(client, ledger, EMPRESA, request_pf(), confirm=True)
    assert ledger.get(EMPRESA, 'venda-1')['status'] == 'erro'
    emit_session.on('GET', EMIT_PATH, lambda **_: response(
        '<html>ok</html>', url=f'{BASE}{APP}/DPS/NFSe/Concluida?chaveAcesso={emit_session.key}'
    ))
    assert operations.emit(client, ledger, EMPRESA, request_pf(), confirm=True)['emitida'] is True
    assert [event['evento'] for event in ledger.history(EMPRESA, 'venda-1')] == ['recusada', 'emitida']


def test_possible_duplicate_on_the_portal_discards_the_draft(emit_session, ledger):
    emit_session.on('GET', f'{APP}/Notas/Emitidas', lambda params=None, **_: response(
        load('emitidas.html') if (params or {}).get('pg') is None else '<table><tbody></tbody></table>'
    ))
    with pytest.raises(NfseValidationError, match='mesmo tomador e valor'):
        operations.emit(EmissorNacionalClient(emit_session), ledger, EMPRESA, request_pf(), confirm=True)
    assert ledger.get(EMPRESA, 'venda-1')['status'] == 'erro'
    assert emit_session.count('POST', DRAFT_DELETE) == 1
    assert emit_session.count('GET', EMIT_PATH) == 0


def test_emission_without_reference_gets_an_automatic_one(emit_session, ledger):
    client = EmissorNacionalClient(emit_session)
    summary = operations.emit(client, ledger, EMPRESA, request_pf(referencia=''), confirm=True)
    assert summary['referencia'].startswith('auto-')
    assert ledger.by_chave(emit_session.key)['referencia'] == summary['referencia']


def test_cancel_preview_and_confirmed_cancel_update_the_record(cancel_session, chave, ledger):
    ledger.start(empresa=EMPRESA, referencia='venda-1', tomador_documento='52998224725', valor=Decimal('1047.51'),
                 competencia=date(2026, 11, 9), draft_id='DRAFT0001')
    ledger.update(EMPRESA, 'venda-1', status='emitida', chave_acesso=chave)
    client = EmissorNacionalClient(cancel_session)
    preview = operations.cancel(client, ledger, EMPRESA, chave, motivo='2', justificativa=JUSTIFICATIVA)
    assert preview['previa'] is True and preview['cancelamento_direto'] is True
    assert cancel_session.count('POST', CANCEL_PATH) == 0
    assert ledger.get(EMPRESA, 'venda-1')['status'] == 'emitida'

    result = operations.cancel(client, ledger, EMPRESA, chave, motivo='2', justificativa=JUSTIFICATIVA, confirm=True)
    assert result['cancelada'] is True and result['referencia'] == 'venda-1'
    assert result['situacao_apos'] == 'NFS-e cancelada'
    assert ledger.get(EMPRESA, 'venda-1')['status'] == 'cancelada'


def test_cancelled_reference_needs_explicit_reissue(emit_session, ledger):
    ledger.start(empresa=EMPRESA, referencia='venda-1', tomador_documento='52998224725', valor=Decimal('1047.51'),
                 competencia=date(2026, 11, 9), draft_id='DRAFT0001')
    ledger.update(EMPRESA, 'venda-1', status='cancelada', chave_acesso='1' * 50)
    client = EmissorNacionalClient(emit_session)
    with pytest.raises(DuplicateReferenceError, match='reemitir'):
        operations.emit(client, ledger, EMPRESA, request_pf(), confirm=True)
    summary = operations.emit(client, ledger, EMPRESA, request_pf(), confirm=True, reemitir=True)
    assert summary['chave_acesso'] == emit_session.key
    assert ledger.history(EMPRESA, 'venda-1')[-1]['evento'] == 'reemitida'


def test_register_existing_note_and_refuse_conflicts(emit_session, ledger):
    client = EmissorNacionalClient(emit_session)
    summary = operations.register_existing(client, ledger, EMPRESA, 'venda-9', emit_session.key)
    assert summary['numero_nfse'] == str(int(emit_session.key[23:36]))
    assert ledger.get(EMPRESA, 'venda-9')['status'] == 'emitida'
    with pytest.raises(DuplicateReferenceError, match='venda-9'):
        operations.register_existing(client, ledger, EMPRESA, 'venda-10', emit_session.key)
    emit_session.on('GET', f'{APP}/Notas/Visualizar/Index', load('nota-cancelada.html'))
    cancelled_key = re.search(r'\d{50}', load('nota-cancelada.html')).group(0)
    with pytest.raises(NfseError, match='cancelada'):
        operations.register_existing(client, ledger, EMPRESA, 'venda-11', cancelled_key)


def test_discard_reference_only_without_a_valid_note(ledger):
    ledger.start(empresa=EMPRESA, referencia='venda-1', tomador_documento='52998224725', valor=Decimal('10'),
                 competencia=date(2026, 11, 9), draft_id='DRAFT0001')
    ledger.update(EMPRESA, 'venda-1', status='incerta')
    preview = operations.discard_reference(ledger, EMPRESA, 'venda-1')
    assert preview['previa'] is True and ledger.get(EMPRESA, 'venda-1')
    assert operations.discard_reference(ledger, EMPRESA, 'venda-1', confirm=True)['descartada'] is True
    assert ledger.get(EMPRESA, 'venda-1') is None
    assert ledger.history(EMPRESA, 'venda-1')[-1]['evento'] == 'descartada'

    ledger.start(empresa=EMPRESA, referencia='venda-2', tomador_documento='52998224725', valor=Decimal('10'),
                 competencia=date(2026, 11, 9), draft_id='DRAFT0002')
    ledger.update(EMPRESA, 'venda-2', status='emitida', chave_acesso='2' * 50)
    with pytest.raises(NfseError, match='fica no registro'):
        operations.discard_reference(ledger, EMPRESA, 'venda-2', confirm=True)
    with pytest.raises(NfseError, match='não está no registro'):
        operations.discard_reference(ledger, EMPRESA, 'inexistente', confirm=True)


def test_ledger_listing_filters(ledger):
    for index, status in enumerate(('emitida', 'erro', 'emitida'), start=1):
        ledger.start(empresa=EMPRESA if index < 3 else 'outra', referencia=f'venda-{index}',
                     tomador_documento='52998224725', valor=Decimal('10'), competencia=date(2026, 11, 9),
                     draft_id=None)
        ledger.update(EMPRESA if index < 3 else 'outra', f'venda-{index}', status=status)
    assert len(ledger.list()) == 3
    assert [row['referencia'] for row in ledger.list(EMPRESA, status='emitida')] == ['venda-1']
    assert len(ledger.list('outra')) == 1
    with pytest.raises(ValueError, match='desconhecidos'):
        ledger.update(EMPRESA, 'venda-1', campo='x')
    with pytest.raises(KeyError):
        ledger.update(EMPRESA, 'inexistente', status='erro')
