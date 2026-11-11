# Cancelling an NFS-e

Cancelling is a fiscal act and cannot be undone. A cancelled invoice stays visible on the portal with a
cancellation event; a replacement is a new invoice with a new number.

## Sequence

1. Identify the invoice: the access key (50 digits). From a reference: `nfseai registro --referencia X`.
   From a customer: `nfseai emitidas --busca "<nome do tomador>"` (the search matches the customer's name
   only, not the document, the key or the number).
2. Preview, without `--confirm`:

   ```bash
   nfseai cancelar <chave> --motivo nao-prestado --justificativa "Serviço não prestado: pedido 1042 cancelado pelo cliente em 10/11/2026."
   ```

   The JSON has `previa: true`, `cancelada: false`, `cancelamento_direto`, `tipo_evento`, `mensagem` and the
   reasons the portal offers (`motivos`).
3. Show the user which invoice will be cancelled (number, customer, amount) and the justification text.
4. After an explicit yes, repeat with `--confirm`.
5. Report `cancelada: true` and `situacao_apos`. The client re-reads the invoice and fails if the portal
   said "success" but the invoice still shows no cancellation event.

`nfseai cancelar-previa <chave>` gives the same preview without needing a reason.

## Reasons (`--motivo`)

| Value | Portal option | Use for |
|---|---|---|
| `erro` | 1 - Erro na emissão | wrong customer, wrong amount, duplicated invoice |
| `nao-prestado` | 2 - Serviço não prestado | refund, order cancelled before the service |
| `outros` | 9 - Outros | anything else, explained in the justification |

## Justification

15 to 255 characters. It is a statement to the tax authority, kept with the invoice. Write what actually
happened, in Portuguese, with the fact and the date when relevant. Ask the user for the reason; do not
make one up.

## Deadline and fiscal analysis

Direct cancellation is event 101101. When the invoice is outside the window the municipality allows, the
portal offers only event 101103: a request for fiscal analysis by the municipality, which may be denied. The
preview shows `cancelamento_direto: false` and the portal's message. The command then refuses with
`CancellationNotAllowed` unless `--permitir-analise-fiscal` is given. That request path was read from the
portal but **not validated end to end**; recommend that the user files it in the portal by hand or talks to
their accountant, and use the flag only on their explicit decision.

## After cancelling

- The ledger row of that invoice (if any) becomes `cancelada`.
- To issue a corrected invoice for the same reference: rehearse and emit with `--reemitir`. The history keeps
  both access keys (`nfseai registro --referencia X`).
- A PDF generated before the cancellation no longer reflects the invoice. Tell the user to discard it.

## Things the portal does that surprise

- A cancelled invoice still shows "100 - NFS-e Gerada" in its original situation field. The cancellation is
  a separate panel ("Evento de Cancelamento de NFS-e"); `cancelada: true` in `nfseai nota` reads that event.
- The portal's cancellation form opens "successfully" even for an invoice that is already cancelled. The
  client checks first and refuses with "já está cancelada".
- If the portal asks for a digital-certificate signature of the event, the client stops with
  `UnsupportedScenario`: that happens with certificate logins, not with password logins.
