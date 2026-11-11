# What nfseai covers and what it refuses

nfseai reproduces the portal's "emissão completa" (the four screens Pessoas, Serviço, Valores, Emitir NFS-e)
for one fiscal scenario that was mapped field by field from real portal traffic and validated with real
invoices. Outside that scenario the form asks for other fields, and guessing them could produce a wrong
fiscal document. So the client refuses, with `UnsupportedScenario`, before sending anything.

## Supported

| Aspect | Supported value |
|---|---|
| Login | CNPJ and password of the Emissor Nacional (a CPF login is accepted by the client but was not validated) |
| Issuer | Simples Nacional, ME/EPP (the portal's `recuperaropcaosn` returns type 3) |
| Simples regime | 1: federal and municipal taxes collected through Simples Nacional |
| Issuing municipality | exactly one for the CNPJ, with an active agreement ("convênio") with the national system |
| Customer (tomador) | in Brazil, CPF (validated with real invoices) or CNPJ (validated up to the conference screen) |
| Service | any national service code and NBS the portal accepts, taxable operation, ISSQN due |
| Place of service | the issuer's municipality (validated); another Brazilian municipality by IBGE code is implemented but not yet validated with a real invoice |
| ISSQN retention | none |
| Tax estimate | Simples Nacional rate informed (validated); "not informed" is implemented but not yet validated with a real invoice |
| Emission mode | "DPS" without signer (password login) |
| Cancellation | direct cancellation (event 101101) |
| Queries | issued and received invoices, detail, drafts |
| PDF | the portal's print version rendered to PDF |

## Refused (`UnsupportedScenario`) or not available

| Case | Why |
|---|---|
| MEI (simplified emission) | different screen (`/DPS/Simplificada`), not mapped |
| Companies outside Simples Nacional, or Simples with regime 2/3 | the Valores screen asks for ISSQN rate and federal taxes; not mapped |
| ISSQN retention, deductions, municipal benefits, suspension | not mapped |
| IBS/CBS group ("Preencher as informações IBS/CBS?" = yes) | not mapped; the client answers "no" |
| Customer abroad, intermediary, government purchase | not mapped |
| Export, immunity, non-incidence | not mapped |
| Construction works and events (obra, evento) | extra mandatory blocks, not mapped |
| A CNPJ with more than one issuing municipality | not mapped |
| A municipality without an active agreement with the national system | refused by the client; not tested |
| Substitution of an invoice | route read, not implemented; cancel and issue again |
| Fiscal-analysis cancellation request (event 101103) | route read, not validated |
| Official DANFSe and XML download | behind hCaptcha on the portal; never bypassed |
| Login by digital certificate or gov.br | not implemented |
| Alphanumeric CNPJ | not accepted by the local validation yet |

When a user's company falls in a refused case, say which one, and that they can still use the queries, the
PDF and (when the invoice was issued by hand) the ledger. Contributions that map a new scenario are welcome
in the repository; the procedure is in `docs/remapeamento.md` there.

## How the client protects the user when the portal changes

- **Contract check.** Before each form step, the field names of the live form are compared with the mapped
  contract. A new or missing field raises `PortalChangedError` before any POST.
- **Hidden-flag check.** Hidden inputs that characterize the scenario (no export group, no mandatory
  construction/event block, no special ISSQN regime, no IBS/CBS) must have the mapped values.
- **Conference check.** After the three steps, the portal's summary must show the logged-in issuer, the
  requested customer, amount and competência, both when preparing and again right before emitting.
- **Readback.** After emitting, the invoice is fetched by its access key and its customer and amount are
  compared with the request.

## Other ways to issue NFS-e

- **Official API (Sefin Nacional / ADN)** with an ICP-Brasil digital certificate (mTLS, signed XML). It
  returns the fiscal XML, has a test environment and native idempotency. It needs an e-CNPJ A1 and an XML
  signing stack. nfseai does not implement it.
- **Municipal systems.** Municipalities that have not joined the national issuer keep their own portals.
  nfseai does not talk to them.
