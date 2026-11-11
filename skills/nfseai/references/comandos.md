# Command reference

Every command prints JSON on stdout, except the two interactive wizards. Errors go to stderr as
`erro: <Type>: <message>`. `--empresa <apelido>` and `--env-file <arquivo>` are accepted before or after the
command name.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | error (message on stderr; for `status`/`doctor`, also in the JSON) |
| 2 | wrong usage (missing option, interactive wizard without a terminal) |
| 3 | the company is not connected: credential handoff needed |
| 4 | fiscal defaults incomplete |

## Connection and configuration

| Command | What it does |
|---|---|
| `nfseai conectar [--empresa X] [--sem-teste] [--padrao] [--sem-configurar]` | **User only, in a terminal.** Asks CNPJ and password, tests the login once, saves. Alias: `connect` |
| `nfseai status [--online]` | Connection and fiscal defaults of the company in use. `--online` logs in and returns the taxpayer name. Exit 0/3/4/1 |
| `nfseai empresas` | Companies on this machine, which are connected, which is the default |
| `nfseai usar <apelido>` | Sets the default company |
| `nfseai configurar` | Wizard for fiscal defaults (user, in a terminal) |
| `nfseai configurar --codigo-servico C --nbs N --descricao "..." (--aliquota-sn P \| --sem-valor-tributos) [--info-complementar T] [--municipio-prestacao IBGE --nome-municipio-prestacao "Nome/UF"]` | Saves fiscal defaults without questions; only the options passed change. Exit 4 while something is still missing |
| `nfseai desconectar --confirm` | Deletes the saved password and session of the company. Alias: `disconnect` |
| `nfseai doctor` | Checks Python, dependencies, permissions of `~/.nfseai`, portal reachability, PDF support |
| `nfseai instalar-pdf` | Installs Playwright and headless Chromium into nfseai's environment (about 300 MB) |
| `nfseai --version` | Version |

`status` JSON: `empresa`, `conectada`, `origem_credenciais` (`arquivo`, `ambiente` or null), `login`,
`empresa_padrao`, `empresas`, `perfil_fiscal` (`completo`, `faltando`, and each value), `suporte_pdf`,
`pasta`, `versao`, `proximo_passo` (what to do next, in Portuguese), and `portal` with `--online`.

## Lookups (read only)

| Command | Output |
|---|---|
| `nfseai whoami` | `inscricao`, `nome` of the logged-in taxpayer |
| `nfseai servicos "<termo>"` | list of `{id, text}`: national service codes matching a word or a code |
| `nfseai municipios "<termo>"` | list of `{id, text}`: IBGE code and "Nome/UF" |
| `nfseai emitidas [--inicio D --fim D] [--busca NOME] [--pagina N] [--todas]` | one page (15 rows) of issued invoices: `notas`, `total`, `pagina`, `ultima_pagina`. With `--todas` (needs both dates): a flat list of every invoice in the period |
| `nfseai recebidas [--inicio D --fim D] [--busca NOME] [--pagina N]` | invoices received by the company |
| `nfseai nota <chave>` | `numero_nfse`, `situacao`, `cancelada`, `data_geracao`, `valor_servico`, `tomador_documento`, `tomador_nome`, `numero_dps`, `serie_dps`, and `secoes` with every label/value of the invoice page |
| `nfseai rascunhos` | drafts on the portal: `id`, `tipo`, `atualizado_em`, `competencia`, `emitida_para`, `valor`, `passo` |

Listing facts: the date filter uses the generation date and accepts at most 30 days per request (`--todas`
walks longer periods in windows); without dates the portal shows the last 30 days; `--busca` matches the
customer's name only. Each row has `chave_acesso`, `data_geracao`, `tomador_documento`, `tomador_nome`,
`competencia`, `municipio`, `valor`, `situacao_codigo` (`P100_GERADA` active, `P104_NFSE_CANCELADA`
cancelled), `situacao`, `pode_cancelar`, `pode_substituir`.

## Emission

| Command | What it does |
|---|---|
| `nfseai preparar --documento D --valor V [...]` | Rehearsal without the ledger: builds the draft, returns `resumo`, discards the draft (`--manter` keeps it on the portal) |
| `nfseai emitir --documento D --valor V [--referencia R] [...]` | Rehearsal with the ledger check: `ensaio: true`, `emitida: false` |
| `nfseai emitir ... --confirm [--pdf [ARQUIVO]] [--reemitir] [--permitir-duplicidade]` | **Fiscal act.** Emits, reads back, records. `emitida: true`, `chave_acesso`, `numero_nfse`, `numero_dps`, `serie_dps`, `data_geracao`, `situacao`, `valor_servico`, and `pdf` or `pdf_erro` |

Emission options are listed in `emissao.md`.

## Cancellation

| Command | What it does |
|---|---|
| `nfseai cancelar-previa <chave>` | Whether the invoice can be cancelled directly, and the reasons offered |
| `nfseai cancelar <chave> --motivo erro\|nao-prestado\|outros --justificativa "..."` | Preview: `previa: true`, `cancelada: false` |
| `nfseai cancelar ... --confirm [--permitir-analise-fiscal]` | **Fiscal act.** `cancelada`, `situacao_apos`, `tipo_evento`, `mensagem`, `referencia` |

## Files

| Command | What it does |
|---|---|
| `nfseai pdf <chave> [--out ARQUIVO]` | PDF of the portal's print version. Default path `~/.nfseai/pdf/NFSe_<numero>_<chave>.pdf`. Returns `arquivo`, `bytes` |
| `nfseai impressao <chave> --out ARQUIVO.html` | The print version as HTML (no browser needed) |

## Ledger

| Command | What it does |
|---|---|
| `nfseai registro [--status S] [--limite N] [--todas-empresas]` | Rows of the local ledger, newest first |
| `nfseai registro --referencia R` | One row with its `historico` |
| `nfseai registrar --referencia R --chave <chave>` | Attaches an invoice that already exists on the portal to a reference |
| `nfseai registro-descartar --referencia R [--confirm]` | Frees a reference that has no valid invoice (`preparada`, `incerta`, `erro`); preview without `--confirm` |
| `nfseai rascunho-excluir <id> --confirm` | Deletes a draft on the portal. Only delete drafts this session created: the user or their accountant may have drafts in progress |
