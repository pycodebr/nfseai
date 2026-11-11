# Emitting an NFS-e

## The sequence

1. `nfseai status` exits 0 (connected, fiscal defaults complete).
2. Collect from the user: customer CPF/CNPJ, amount, service date (competência) if it is not today, the
   reference of the sale in their system, and a description if this invoice needs a different one.
3. **Rehearse**: `nfseai emitir ...` without `--confirm`.
4. Show the user, in plain Portuguese: customer name (as the Receita Federal has it), document, amount,
   competência, service description, issuing company. Ask: "Posso emitir esta nota?"
5. After an explicit yes: the same command plus `--confirm` (and `--pdf` if they want the file).
6. Report: NFS-e number, access key, situation, amount, and the PDF path when there is one.

`nfseai preparar` is the same rehearsal without touching the ledger; use it for a first test of a company's
fiscal defaults ("ensaio").

## Fields

| Option | Meaning | Default |
|---|---|---|
| `--documento` | customer (tomador) CPF or CNPJ, with or without punctuation | required |
| `--valor` | service amount: `1500.00`, `1500,00` or `1.500,00` | required |
| `--competencia` | date the service was rendered, `DD/MM/AAAA` or `AAAA-MM-DD` | today |
| `--referencia` | identifier of the sale/contract/order in the user's system | none (auto-generated) |
| `--descricao` | service description printed on the invoice, 1 to 2000 characters | company default |
| `--codigo-servico` | national service code (código de tributação nacional), `08.02.01` | company default |
| `--nbs` | NBS code, 9 digits | company default |
| `--aliquota-sn` | Simples Nacional rate in %, shown as the tax estimate | company default |
| `--sem-valor-tributos` | do not inform a tax estimate | company default |
| `--info-complementar` | free text in "informações complementares", up to 2000 characters | empty |
| `--municipio-prestacao`, `--nome-municipio-prestacao` | IBGE code and name when the service is rendered in another municipality | the company's municipality |
| `--nome`, `--email`, `--telefone` | customer contact; `--nome` is used only when the Receita Federal returns no name | empty |
| `--pdf [arquivo]` | render the PDF after emitting | off |

What the tool fills by itself, from the portal: the company's registration data, its municipality, its
Simples Nacional option, the customer's name (and address, for a CNPJ) from the Receita Federal, and the
ISSQN incidence for the municipality and service.

## Reading the rehearsal

```json
{
  "empresa": "minha-empresa",
  "referencia": "pedido-1042",
  "tomador_documento": "11.444.777/0001-61",
  "tomador_nome": "TOMADORA EXEMPLO LTDA",
  "valor": "1500.00",
  "competencia": "2026-11-10",
  "resumo": {
    "Informações Gerais": {"Data de Competência": "10/11/2026"},
    "Informações do Emitente": {"CNPJ": "11.222.333/0001-81"},
    "Tomador/Adquirente do Serviço": {"CNPJ": "11.444.777/0001-61"},
    "Serviço Prestado": {"...": "..."},
    "Valores do Serviço Prestado": {"Valor da operação/serviço prestado": "R$ 1.500,00"},
    "Valor líquido da NFS-e": {"Valor líquido da NFS-e": "R$ 1.500,00"}
  },
  "ensaio": true,
  "emitida": false,
  "rascunho_descartado": true,
  "proximo_passo": "nada foi emitido. Mostre o resumo ao usuário e, com a autorização dele para esta nota, repita o comando com --confirm"
}
```

`resumo` is the portal's own conference screen ("Emitir NFS-e"), read after the three form steps. The client
already compared issuer, customer, amount and competência with the request and would have failed otherwise.
Read it anyway and show the user what matters. `ensaio: true` and `emitida: false` mean nothing fiscal
happened.

The ISSQN fields usually show `-` for a Simples Nacional company in regime 1: the municipal tax is collected
inside the Simples guide. The informed rate appears under the tax estimate. That is expected.

## The reference and the ledger

The portal has no idempotency: two emission calls make two invoices. `--referencia` is the key of the local
ledger (`~/.nfseai/ledger.sqlite3`), one row per company and reference:

| Status | Meaning | A new `emitir` for the same reference |
|---|---|---|
| `emitida` | invoice exists | refused (`DuplicateReferenceError`, with the access key) |
| `cancelada` | invoice was cancelled | refused unless `--reemitir` |
| `incerta` | emission sent, result not confirmed | refused until resolved |
| `preparada` | a previous run stopped between the draft and the result | refused until resolved |
| `erro` | the portal rejected it or it was not sent | allowed |

Use the identifier the user's system already has (order number, sale id, contract + month for recurring
services, such as `contrato-17-2026-11`). Do not invent variations of a reference to get past a refusal.
Without `--referencia` the emission is recorded under an automatic one (`auto-...`) and only the portal
check below protects against a duplicate.

`nfseai registro` lists the ledger; `nfseai registro --referencia X` shows one row with its history.

## The duplicate check on the portal

Before sending, the client lists the company's issued invoices of the last 30 days and stops if an active
one has the same customer and the same amount ("já existe NFS-e ativa para o mesmo tomador e valor"). The
message lists the access keys. Look at them (`nfseai nota <chave>`), tell the user, and only if they confirm
it is a different sale repeat with `--permitir-duplicidade`. Recurring monthly invoices of the same amount
to the same customer hit this check every month: that is the case the flag exists for, with the user aware.

## Uncertain result

`EmissionUncertainError` means the emission request was sent and the answer did not bring a confirmed access
key (network failure, unexpected page). The invoice may or may not exist. The ledger marks the reference
`incerta`.

1. Do **not** run `emitir` again.
2. `nfseai emitidas` (today's window) and look for the customer document and amount.
3. Found: `nfseai registrar --referencia X --chave <chave>` attaches the existing invoice to the reference
   (it checks the invoice is active). Report it as emitted.
4. Not found after a few minutes: tell the user. With their agreement,
   `nfseai registro-descartar --referencia X --confirm` frees the reference, and the emission starts again
   from the rehearsal.
5. In doubt, the user checks the portal in the browser. A duplicate costs more than a delay.

The same steps apply to a reference stuck in `preparada` (the process died mid-emission). The raw portal
answer of each emission is saved in `~/.nfseai/debug/` for diagnosis; it contains fiscal data.

## Invoices emitted by hand

When the user emitted an invoice directly on the portal and wants the ledger to know (so that the sale is
never emitted again): `nfseai registrar --referencia <id-da-venda> --chave <chave>`.

## Emitting a batch

- Get the list from the user (document, amount, competência, reference for each). Rehearse each one and show
  a table. Authorization for the batch must name the batch ("pode emitir essas 12 notas").
- Emit **one at a time, sequentially**. In our measurements a rehearsal took about 20 seconds and the PDF
  about 30 seconds more, so budget around a minute per invoice with `--pdf`, and run long batches in the
  background. Do not run two `nfseai` commands for the same company in parallel.
- Stop at the first non-zero exit and report. Never loop over failures.
- Close with a readback: `nfseai registro --status emitida`, and `nfseai emitidas` for the period, compared
  with the list. Report per invoice: reference, number, access key, amount.

## Who the invoice is issued to

- The customer is identified by the CPF/CNPJ. The name printed is the one the Receita Federal holds for that
  document, even when the user's system has another name. Tell the user when they differ.
- For a CPF the address is optional and left empty. For a CNPJ the portal brings the registered address and
  the client sends it, as the screen does.
- Customers abroad are not supported (`UnsupportedScenario`).
