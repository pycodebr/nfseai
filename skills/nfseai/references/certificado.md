# Path A: the official Sefin Nacional API with a digital certificate

nfseai talks to the national NFS-e system in two ways. Each company uses one of them:

| | Path B (portal) | Path A (official API) |
|---|---|---|
| Login | CNPJ and password of the Emissor Nacional | e-CNPJ A1 digital certificate (`.pfx`) |
| Connect with | `nfseai conectar` | `nfseai conectar --certificado <file.pfx>` |
| Issuer | Simples Nacional ME/EPP | outside Simples Nacional (lucro real or presumido) |
| ISSQN withheld by the customer | no | yes (`--reter-iss`) |
| Official XML | no (captcha on the portal) | yes (`nfseai xml`) |
| Test environment | no (rehearsal only) | yes: `producao-restrita`, invoices without fiscal value |

`nfseai status` shows the path of the company in `caminho` (`portal` or `sefin`).

**Status: validated on `producao-restrita` and in production.** Emission, lookup, the official XML, the DPS
lookup and cancellation were exercised on `producao-restrita` with a real company certificate, and invoices were
then issued in production with the certificate of a company outside Simples Nacional (`docs/caminho-a.md`).
Start companies on `--ambiente producao-restrita` and move to production only when the user decides, after the
checklist in "Going to production" below. `producao-restrita` already applies the NT 009 layout; production
keeps the layout published in February until its switch, and nfseai picks the layout by environment.

## Connecting a company (the user does it)

Certificate support is optional. If `nfseai conectar --certificado` reports it is not installed, run
`nfseai instalar-certificado` (it installs the `cryptography` package into nfseai's environment).

The certificate password follows the same rule as the portal password: **never in chat, never in a flag,
never in a file you write.** The user runs, in their own terminal:

```bash
nfseai conectar --certificado ~/empresa.pfx --empresa minha-empresa --municipio 3550308
```

- The wizard asks for the certificate password without echo, shows the holder, CNPJ and expiry date, and asks
  the user to confirm it is the right company. Nothing is saved if the password is wrong, the certificate
  expired or it has no ICP-Brasil CNPJ.
- `--municipio` is the IBGE code of the issuing municipality (asked if omitted). `--inscricao-municipal` is
  needed when the municipality requires it on the invoice. `--serie` is the DPS series (1 to 49999, default 1);
  in production, use a series no other system of the company uses (see "DPS series" below).
- `--ambiente` defaults to `producao-restrita`. Production needs `--ambiente producao`, an explicit decision of
  the user.
- The `.pfx` is copied to `~/.nfseai/certificados/<company>.pfx` (mode 600) and the password goes to
  `credentials.json`. To open the connection, the key is written for an instant in `~/.nfseai/tls/` (mode
  600) and deleted before any request.
- Servers and containers: `NFSEAI_<COMPANY>_CERT` (path to the `.pfx`) and `NFSEAI_<COMPANY>_CERT_PASSWORD`,
  or `NFSEAI_CERT` and `NFSEAI_CERT_PASSWORD`, from the platform's secret store.
- Keep `~/.nfseai` (or `NFSEAI_HOME`) on persistent storage. The DPS numbering lives in `ledger.sqlite3`: a
  container that starts with an empty ledger restarts the numbering at 1 in the same series, and the Sefin
  refuses the numbers already used (E0014).
- `nfseai desconectar --confirm` deletes the certificate copy and its password.

## DPS series and layout

**Series.** The Sefin identifies each DPS by municipality, CNPJ, series and number, and refuses a repeated one
(E0014). The default series is 1, which a company that already issued invoices through another system (an ERP,
an earlier issuer, its own software) may have used. Before production, ask the user (or the accountant) which
series the company already uses, or check the series of past invoices, and pick one used only by nfseai.
Change it without reconnecting the certificate:

```bash
nfseai configurar --serie 100
```

The numbering of the new series starts in the local ledger; the numbering of the previous series is kept.

**Layout.** Version 1.01 has two layouts: the one published in February and the NT 009 one (tax reform).
`producao-restrita` already applies NT 009; production keeps the February layout until its announced switch.
nfseai picks the layout by environment, and `nfseai status` shows it in `leiaute`. When production switches,
or when the Sefin refuses with E1235, force it:

```bash
nfseai configurar --leiaute 1.01-nt009     # --leiaute padrao goes back to the environment's layout
```

## Going to production

1. Run the whole cycle on `producao-restrita`: rehearsal, emission, `nfseai xml`, `nfseai dps` and a
   cancellation.
2. Keep that company as the test profile and connect the production one under another name, with
   `--ambiente producao` and the chosen `--serie`. The user decides this step.
3. Confirm every fiscal default with the accountant (service code, approximate taxes, IRRF, ISSQN). The ISSQN
   withholding required on `producao-restrita` may differ from production; in production, follow the accountant.
4. Rehearse the first production invoice and show it to the user before `--confirm`.

## Fiscal defaults

Path A needs, from the company's accountant: national service code, NBS, description and the approximate tax
percentages of the invoice (federal, state, municipal; Lei 12.741/2012). Companies outside Simples Nacional
cannot leave that total blank.

```bash
nfseai configurar --codigo-servico 08.02.01 --nbs 122051900 --descricao "..." \
  --tributos-federais 11,30 --tributos-estaduais 0 --tributos-municipais 5
```

`--aliquota-irrf 1,5` records the IRRF withheld by the customer. Each emission computes the amount, truncated
to the cent (R$ 1,234.50 x 1.5% = R$ 18.51), shows it in the rehearsal and deducts it from the net value.
Amounts of R$ 10.00 or less are not withheld (Lei 9.430/1996, art. 67). `--sem-irrf` emits one invoice without it. Whether a customer withholds IRRF comes from the accountant or the
contract, not from you. CP, CSLL and PIS/COFINS withholding are not supported yet.

The NBS is optional on path A: the Sefin accepts invoices without it.

`--aliquota-iss` goes on the invoice only when the municipality of incidence requires it. With an active
agreement with the national system, the Sefin takes the rate from the municipality's parameters and refuses an
informed rate (rule E0506). Do not add it on your own.

## Emitting

The same commands and the same rules as path B: rehearse, show, get an explicit yes, `--confirm`, read back.

```bash
nfseai emitir --documento 11.444.777/0001-61 --nome "TOMADORA EXEMPLO LTDA" --valor 1500,00 --referencia pedido-1042
nfseai emitir ... --confirm
```

- The API does not look up the customer's name: `--nome` is required.
- `--reter-iss` marks the ISSQN as withheld by the customer. It requires the customer's address:
  `--tomador-cep`, `--tomador-municipio` (IBGE), `--tomador-logradouro`, `--tomador-numero`,
  `--tomador-bairro` and, optionally, `--tomador-complemento`. Ask the user whether the customer withholds;
  it comes from the contract, not from you.
- **The rehearsal sends nothing to the Sefin.** It builds the DPS, validates it against the official XSD,
  signs it and shows the summary. The DPS number is reserved only when emitting, in the local ledger.
- The result has `chave_acesso`, `numero_nfse`, `serie_dps` and `numero_dps`.

## Uncertain result

The API identifies each DPS by issuer, series and number, and refuses a second DPS with the same identifier
(E0014). When the connection drops during an emission, the client already asks the Sefin whether that DPS
generated an invoice and returns it if so. When it cannot confirm, it raises `EmissionUncertainError` with the
DPS identifier. Never retry: run `nfseai dps <identifier>` later. If the invoice exists, associate it with
`nfseai registrar --referencia <ref> --chave <chave>`; if it does not, report to the user before anything else.

## Commands on path A

| Command | Path A |
|---|---|
| `status`, `empresas`, `configurar`, `preparar`, `emitir`, `nota`, `cancelar-previa`, `cancelar`, `registro`, `registrar`, `registro-descartar` | same as path B |
| `xml <chave> --out <file>` | saves the official NFS-e XML |
| `dps <identifier>` | asks the Sefin whether a DPS generated an invoice |
| `emitidas`, `recebidas`, `rascunhos`, `whoami`, `servicos`, `municipios`, `pdf`, `impressao` | portal only; refused with exit 2 |

## Errors specific to path A

| Error | Do |
|---|---|
| `CertificateError` | Expired certificate, another CNPJ or wrong password. The user renews or reconnects with `nfseai conectar --certificado`. |
| `EmissionRejectedError` with codes `E....` | The Sefin refused the DPS. Report the codes and descriptions; most come from the fiscal data, which only the accountant can change. For known codes the message ends with what to do (`dicas` in Python). |
| `E0014` (DPS repeated) | The series and number were already used: another system of the company uses the same series, or the local ledger was recreated. Pick a series only nfseai uses (`nfseai configurar --serie N`). Never resend the same invoice. |
| `E1235` (layout) | The DPS does not follow the layout the environment applies (NT 009 switch). Check `leiaute` in `nfseai status` and run `nfseai configurar --leiaute 1.01-nt009` (or `1.01`). |
| `UnsupportedScenario` | Outside the mapped scenario of path A (Simples Nacional issuer, special regime, IBS/CBS). |
