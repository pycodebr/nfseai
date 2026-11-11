"""Operações completas: emissão, cancelamento e registro de notas, sempre com o registro local.

A camada `client` fala com o portal; esta camada acrescenta o que protege quem emite:
a referência da venda no registro local (controle contra nota em dobro), o ensaio antes
da emissão e a leitura posterior do resultado.
"""
from .errors import (
    DuplicateReferenceError,
    EmissionRejectedError,
    EmissionUncertainError,
    NfseError,
    NfseValidationError,
)
from .ledger import (
    BLOCKING_STATUSES,
    STATUS_CANCELADA,
    STATUS_EMITIDA,
    STATUS_ERRO,
    STATUS_INCERTA,
    STATUS_PREPARADA,
    auto_reference,
)

ORIENTACAO = {
    STATUS_EMITIDA: 'a nota já foi emitida para esta referência',
    STATUS_CANCELADA: 'a nota desta referência foi cancelada; uma nova nota exige reemitir=True (--reemitir)',
    STATUS_INCERTA: (
        'a emissão anterior ficou sem confirmação. Confira as notas emitidas no portal: se a nota existir, '
        'associe-a com `nfseai registrar`; se não existir, libere a referência com `nfseai registro-descartar`'
    ),
    STATUS_PREPARADA: (
        'uma emissão anterior desta referência foi interrompida antes do resultado. Confira as notas emitidas '
        'no portal: se a nota existir, associe-a com `nfseai registrar`; se não existir, libere a referência '
        'com `nfseai registro-descartar`'
    ),
}


def _check_reference(ledger, empresa, referencia, reemitir):
    existing = ledger.get(empresa, referencia)
    if not existing or existing['status'] not in BLOCKING_STATUSES:
        return False
    if existing['status'] == STATUS_CANCELADA and reemitir:
        return True
    raise DuplicateReferenceError(
        f'a referência "{referencia}" já está no registro como "{existing["status"]}" '
        f'(chave {existing.get("chave_acesso") or "não confirmada"}): {ORIENTACAO[existing["status"]]}.',
        status=existing['status'],
        chave_acesso=existing.get('chave_acesso'),
    )


def emit(client, ledger, empresa, request, *, confirm=False, reemitir=False, permitir_duplicidade=False):
    """Emite a NFS-e de um pedido. Sem ``confirm`` faz só o ensaio: monta o rascunho no portal,
    confere o resumo e descarta o rascunho, sem emitir nada.

    ``reemitir=True`` libera uma nova nota apenas para referência cuja nota anterior foi cancelada.
    """
    explicit_reference = bool(request.referencia)
    referencia = request.referencia or auto_reference()
    reemissao = _check_reference(ledger, empresa, referencia, reemitir) if explicit_reference else False
    prepared = client.emissao.prepare(request)
    summary = {
        'empresa': empresa,
        'referencia': referencia if explicit_reference else None,
        'tomador_documento': request.tomador.documento,
        'tomador_nome': prepared.tomador_nome,
        'valor': str(request.valor),
        'competencia': request.competencia.isoformat(),
        'resumo': prepared.resumo,
    }
    if not confirm:
        summary['ensaio'] = True
        summary['emitida'] = False
        summary['rascunho_descartado'] = client.emissao.discard(prepared)
        return summary
    ledger.start(
        empresa=empresa, referencia=referencia, tomador_documento=request.tomador.documento,
        tomador_nome=prepared.tomador_nome, valor=request.valor, competencia=request.competencia,
        draft_id=prepared.draft_id,
    )
    try:
        result = client.emissao.emit(prepared, confirm=True, permitir_duplicidade=permitir_duplicidade)
    except EmissionUncertainError as exc:
        ledger.update(empresa, referencia, status=STATUS_INCERTA, detalhe=str(exc))
        ledger.log(empresa, referencia, 'incerta', None, str(exc))
        raise
    except EmissionRejectedError as exc:
        ledger.update(empresa, referencia, status=STATUS_ERRO, detalhe=str(exc))
        ledger.log(empresa, referencia, 'recusada', None, str(exc))
        raise
    except NfseValidationError as exc:
        # Recusada antes do envio (ex.: possível duplicidade): o rascunho não tem mais uso.
        client.emissao.discard(prepared)
        ledger.update(empresa, referencia, status=STATUS_ERRO, detalhe=str(exc))
        ledger.log(empresa, referencia, 'nao_enviada', None, str(exc))
        raise
    nota = result.nota
    ledger.update(
        empresa, referencia,
        status=STATUS_EMITIDA,
        chave_acesso=result.chave_acesso,
        numero_nfse=nota.numero_nfse,
        numero_dps=nota.numero_dps,
        serie_dps=nota.serie_dps,
        tomador_nome=nota.tomador_nome or prepared.tomador_nome,
        emitted_at=nota.data_geracao,
    )
    ledger.log(
        empresa, referencia, 'reemitida' if reemissao else 'emitida', result.chave_acesso,
        f'NFS-e {nota.numero_nfse}, DPS {nota.serie_dps}/{nota.numero_dps}, valor {request.valor}',
    )
    summary.update({
        'ensaio': False,
        'emitida': True,
        'referencia': referencia,
        'chave_acesso': result.chave_acesso,
        'numero_nfse': nota.numero_nfse,
        'numero_dps': nota.numero_dps,
        'serie_dps': nota.serie_dps,
        'data_geracao': nota.data_geracao,
        'situacao': nota.situacao,
        'valor_servico': str(nota.valor_servico),
    })
    return summary


def cancel(client, ledger, empresa, chave_acesso, *, motivo, justificativa, confirm=False,
           permitir_analise_fiscal=False):
    """Cancela uma NFS-e e atualiza o registro. Sem ``confirm`` só mostra a prévia."""
    if not confirm:
        preview = client.cancelamento.preview(chave_acesso)
        return {
            'previa': True,
            'cancelada': False,
            'chave_acesso': preview.chave_acesso,
            'cancelamento_direto': preview.cancelamento_direto,
            'tipo_evento': preview.tipo_evento,
            'mensagem': preview.mensagem,
            'motivos': preview.motivos,
        }
    result = client.cancelamento.cancel(
        chave_acesso, motivo=motivo, justificativa=justificativa, confirm=True,
        permitir_analise_fiscal=permitir_analise_fiscal,
    )
    row = ledger.by_chave(chave_acesso)
    cancelada = result.sucesso and result.situacao_apos == 'NFS-e cancelada'
    if row:
        if cancelada:
            ledger.update(row['empresa'], row['referencia'], status=STATUS_CANCELADA,
                          detalhe=f'cancelada: {justificativa}')
        ledger.log(
            row['empresa'], row['referencia'], 'cancelada' if cancelada else 'cancelamento_nao_concluido',
            chave_acesso, f'motivo {motivo}: {justificativa}' if cancelada else result.mensagem,
        )
    return {
        'previa': False,
        'cancelada': cancelada,
        'chave_acesso': chave_acesso,
        'tipo_evento': result.tipo_evento,
        'sucesso': result.sucesso,
        'situacao_apos': result.situacao_apos,
        'mensagem': result.mensagem,
        'referencia': row['referencia'] if row else None,
    }


def register_existing(client, ledger, empresa, referencia, chave_acesso):
    """Associa uma nota que já existe no portal (emitida à mão ou em execução interrompida) a uma referência.

    Confere que a nota está ativa e que não está registrada para outra referência.
    """
    existing = ledger.get(empresa, referencia)
    if existing and existing['status'] in (STATUS_EMITIDA, STATUS_CANCELADA) and \
            existing.get('chave_acesso') != chave_acesso:
        raise DuplicateReferenceError(
            f'a referência "{referencia}" já está no registro como "{existing["status"]}" com outra nota',
            status=existing['status'], chave_acesso=existing.get('chave_acesso'),
        )
    nota = client.notas.get(chave_acesso)
    if nota.cancelada:
        raise NfseError(f'a nota {chave_acesso} está cancelada e não pode ser registrada como emitida')
    other = ledger.by_chave(chave_acesso)
    if other and (other['empresa'], other['referencia']) != (empresa, referencia):
        raise DuplicateReferenceError(
            f'a nota já está registrada para a referência "{other["referencia"]}"',
            status=other['status'], chave_acesso=chave_acesso,
        )
    ledger.start(
        empresa=empresa, referencia=referencia, tomador_documento=nota.tomador_documento,
        tomador_nome=nota.tomador_nome, valor=nota.valor_servico, competencia=nota.competencia, draft_id=None,
    )
    ledger.update(
        empresa, referencia,
        status=STATUS_EMITIDA,
        chave_acesso=chave_acesso,
        numero_nfse=nota.numero_nfse,
        numero_dps=nota.numero_dps,
        serie_dps=nota.serie_dps,
        emitted_at=nota.data_geracao,
        detalhe='nota registrada a partir do portal',
    )
    ledger.log(
        empresa, referencia, 'registrada', chave_acesso,
        f'NFS-e {nota.numero_nfse}, DPS {nota.serie_dps}/{nota.numero_dps}, valor {nota.valor_servico}',
    )
    return {
        'empresa': empresa,
        'referencia': referencia,
        'chave_acesso': chave_acesso,
        'numero_nfse': nota.numero_nfse,
        'valor_servico': str(nota.valor_servico),
        'tomador_nome': nota.tomador_nome,
        'data_geracao': nota.data_geracao,
        'situacao': nota.situacao,
    }


def discard_reference(ledger, empresa, referencia, *, confirm=False):
    """Remove do registro uma referência sem nota válida (preparada, incerta ou erro), após conferência."""
    existing = ledger.get(empresa, referencia)
    if not existing:
        raise NfseError(f'a referência "{referencia}" não está no registro da empresa "{empresa}"')
    if existing['status'] in (STATUS_EMITIDA, STATUS_CANCELADA):
        raise NfseError(
            f'a referência "{referencia}" tem nota {existing["status"]} (chave {existing.get("chave_acesso")}) '
            'e fica no registro'
        )
    if not confirm:
        return {'descartada': False, 'previa': True, 'registro': existing}
    ledger.log(empresa, referencia, 'descartada', existing.get('chave_acesso'),
               f'status anterior: {existing["status"]}')
    return {'descartada': ledger.delete(empresa, referencia), 'previa': False, 'registro': existing}


__all__ = ['cancel', 'discard_reference', 'emit', 'register_existing']
