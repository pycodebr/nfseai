import json
from datetime import date

import pytest

from conftest import APP, CANCEL_PATH, EMPTY_LIST, JUSTIFICATIVA, MODAL_PATH, load, response
from nfseai import (
    MOTIVO_SERVICO_NAO_PRESTADO,
    CancellationNotAllowed,
    CaptchaRequired,
    ConfirmationRequired,
    EmissorNacionalClient,
    NfseValidationError,
    UnsupportedScenario,
)


def test_preview_does_not_submit(cancel_session, chave):
    preview = EmissorNacionalClient(cancel_session).cancelamento.preview(chave)
    assert preview.cancelamento_direto
    assert preview.chave_acesso == chave
    assert cancel_session.count('POST', CANCEL_PATH) == 0
    modal_call = next(kwargs for method, path, kwargs in cancel_session.calls if path == MODAL_PATH)
    assert modal_call['data'] == {'chave': 'ROWKEY0002'}


def test_cancel_requires_confirmation(cancel_session, chave):
    client = EmissorNacionalClient(cancel_session)
    with pytest.raises(ConfirmationRequired):
        client.cancelamento.cancel(chave, motivo=MOTIVO_SERVICO_NAO_PRESTADO, justificativa=JUSTIFICATIVA)
    assert cancel_session.calls == []


def test_cancel_requires_meaningful_justification(cancel_session, chave):
    client = EmissorNacionalClient(cancel_session)
    with pytest.raises(NfseValidationError, match='justificativa'):
        client.cancelamento.cancel(chave, motivo='2', justificativa='curta', confirm=True)
    assert cancel_session.calls == []


def test_cancel_posts_modal_fields_and_reads_back(cancel_session, chave):
    result = EmissorNacionalClient(cancel_session).cancelamento.cancel(
        chave, motivo=MOTIVO_SERVICO_NAO_PRESTADO, justificativa=JUSTIFICATIVA, confirm=True
    )
    body = cancel_session.last_body(CANCEL_PATH)
    assert body['ChaveAcesso'] == chave
    assert body['TipoEvento'] == 'CANCELAMENTO'
    assert body['MotivoCancelamento'] == '2'
    assert body['Justificativa'] == JUSTIFICATIVA
    assert '__RequestVerificationToken' in body
    assert result.sucesso and result.tipo_evento == 101101
    assert result.situacao_apos == 'NFS-e cancelada'
    assert cancel_session.count('POST', CANCEL_PATH) == 1
    cancel_call = next(kwargs for method, path, kwargs in cancel_session.calls if path == CANCEL_PATH)
    assert cancel_call['retry_auth'] is False


def test_cancel_rejects_unknown_reason(cancel_session, chave):
    with pytest.raises(NfseValidationError, match='motivo'):
        EmissorNacionalClient(cancel_session).cancelamento.cancel(
            chave, motivo='7', justificativa=JUSTIFICATIVA, confirm=True
        )
    assert cancel_session.count('POST', CANCEL_PATH) == 0


def test_fiscal_analysis_is_not_requested_by_default(cancel_session, chave):
    cancel_session.on('POST', MODAL_PATH.rstrip('/'), json.dumps({
        'Sucesso': True, 'TipoEvento': 101103, 'Mensagem': 'Prazo para cancelamento expirado.',
        'Html': load('modal-cancelamento.html'),
    }))
    with pytest.raises(CancellationNotAllowed, match='Prazo') as error:
        EmissorNacionalClient(cancel_session).cancelamento.cancel(
            chave, motivo='1', justificativa=JUSTIFICATIVA, confirm=True
        )
    assert error.value.tipo_evento == 101103
    assert cancel_session.count('POST', CANCEL_PATH) == 0


def test_portal_refusal_to_open_cancellation(cancel_session, chave):
    cancel_session.on('POST', MODAL_PATH.rstrip('/'), json.dumps({'Sucesso': False, 'Mensagem': 'NFS-e já cancelada.'}))
    with pytest.raises(CancellationNotAllowed, match='já cancelada'):
        EmissorNacionalClient(cancel_session).cancelamento.preview(chave)


def test_certificate_signature_request_is_unsupported(cancel_session, chave):
    cancel_session.on('POST', CANCEL_PATH, json.dumps({'AssinarPedRegEvento': True}))
    with pytest.raises(UnsupportedScenario, match='certificado'):
        EmissorNacionalClient(cancel_session).cancelamento.cancel(
            chave, motivo='1', justificativa=JUSTIFICATIVA, confirm=True
        )


def test_draft_delete_requires_confirmation(session):
    client = EmissorNacionalClient(session)
    with pytest.raises(ConfirmationRequired):
        client.rascunhos.delete('DRAFT0001')
    assert session.calls == []
    assert client.rascunhos.delete('DRAFT0001', confirm=True) is True


def test_listing_window_and_pagination(session):
    pages = {None: load('emitidas.html'), 2: EMPTY_LIST}
    session.on('GET', f'{APP}/Notas/Emitidas', lambda params=None, **_: response(pages[(params or {}).get('pg')]))
    client = EmissorNacionalClient(session)
    with pytest.raises(ValueError, match='30 dias'):
        client.notas.emitidas(date(2026, 1, 1), date(2026, 3, 1))
    notas = list(client.notas.iter_emitidas(date(2026, 10, 10), date(2026, 11, 9)))
    assert len(notas) == 3
    first = next(kwargs for method, path, kwargs in session.calls if path == f'{APP}/Notas/Emitidas')
    assert first['params'] == {'datainicio': '10/10/2026', 'datafim': '09/11/2026'}


def test_downloads_behind_captcha_are_not_automated(session, chave):
    client = EmissorNacionalClient(session)
    with pytest.raises(CaptchaRequired):
        client.notas.download_xml(chave)
    with pytest.raises(CaptchaRequired):
        client.notas.download_danfse(chave)
    assert session.calls == []


def test_cancelled_note_is_detected_by_the_cancellation_event():
    from nfseai.parsing import parse_nota_detalhe

    nota = parse_nota_detalhe(load('nota-cancelada.html'))
    assert nota.cancelada
    assert nota.situacao == 'NFS-e cancelada'
    assert nota.campo('Outras Informações', 'Situação da NFS-e') == '100 - NFS-e Gerada'
    assert nota.cancelamento['Código da Justificativa'] == '9 - Outros'
    assert nota.cancelamento['Data do Evento'].startswith('09/11/2026')
    assert not parse_nota_detalhe(load('nota.html')).cancelada


def test_already_cancelled_note_cannot_be_cancelled_again(cancel_session, chave):
    cancel_session.cancel_state['cancelled'] = True
    with pytest.raises(CancellationNotAllowed, match='já está cancelada'):
        EmissorNacionalClient(cancel_session).cancelamento.preview(chave)
    assert cancel_session.count('POST', MODAL_PATH) == 0


def test_cancel_confirmed_by_portal_but_note_still_active_is_flagged(cancel_session, chave):
    from nfseai import NfseHttpError

    cancel_session.on('POST', CANCEL_PATH, json.dumps({'TipoEvento': 101101, 'Processado': True, 'Sucesso': True}))
    with pytest.raises(NfseHttpError, match='ainda aparece ativa'):
        EmissorNacionalClient(cancel_session).cancelamento.cancel(
            chave, motivo='9', justificativa=JUSTIFICATIVA, confirm=True
        )
