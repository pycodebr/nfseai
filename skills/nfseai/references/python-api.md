# Using nfseai as a Python library

The CLI is a thin layer over the package. Inside the user's own system (an ERP, a webhook, a script), import
it directly. Install it into that project's environment:

```bash
pip install "nfseai @ git+https://github.com/pycodebr/nfseai"          # add [pdf] for the PDF
```

## Client

```python
from datetime import date
from decimal import Decimal

from nfseai import (
    EmissionRequest, EmissorNacionalClient, Ledger, Servico, Store, Tomador, Tributacao,
    MOTIVO_SERVICO_NAO_PRESTADO, operations,
)

# Credentials of a company connected with `nfseai conectar`, or from NFSEAI_USER / NFSEAI_PASSWORD
with EmissorNacionalClient.from_profile('minha-empresa') as client:
    print(client.whoami())                                  # {'inscricao': '...', 'nome': '...'}

    page = client.notas.emitidas()                          # last 30 days, 15 rows per page
    for nota in client.notas.iter_emitidas(date(2026, 10, 1), date(2026, 11, 10)):
        print(nota.chave_acesso, nota.tomador_nome, nota.valor, nota.cancelada)
    detalhe = client.notas.get(page.notas[0].chave_acesso)  # NotaDetalhe: .numero_nfse, .situacao, .secoes

    pedido = EmissionRequest(
        competencia=date.today(),
        tomador=Tomador(documento='11.444.777/0001-61'),
        valor=Decimal('1500.00'),
        servico=Servico(
            codigo_tributacao_nacional='08.02.01',
            codigo_nbs='122051900',
            descricao='Treinamento prestado conforme contrato 17.',
        ),
        tributacao=Tributacao(tipo_valor_tributos=4, aliquota_sn=Decimal('6.00')),
        referencia='pedido-1042',
    )

    # Low level: two phases, nothing recorded locally
    preparada = client.emissao.prepare(pedido)              # draft on the portal, summary checked
    print(preparada.tomador_nome, preparada.resumo)
    client.emissao.discard(preparada)                       # or: client.emissao.emit(preparada, confirm=True)
```

`from_credentials(user, password)` builds a client from values already in memory (nothing is written to
disk); use it when your system has its own secret store.

## Operations with the ledger (recommended)

```python
store = Store()                                             # ~/.nfseai or NFSEAI_HOME
ledger = Ledger(store.ledger_path)
fiscal = store.fiscal('minha-empresa')                      # defaults saved with `nfseai configurar`

with EmissorNacionalClient.from_profile('minha-empresa') as client:
    pedido = EmissionRequest(
        competencia=date.today(), tomador=Tomador(documento='529.982.247-25'), valor=Decimal('197.00'),
        servico=fiscal.servico(), tributacao=fiscal.tributacao(), referencia='venda-889',
    )
    ensaio = operations.emit(client, ledger, 'minha-empresa', pedido)                  # rehearsal
    resultado = operations.emit(client, ledger, 'minha-empresa', pedido, confirm=True)  # fiscal act
    print(resultado['chave_acesso'], resultado['numero_nfse'])

    operations.cancel(
        client, ledger, 'minha-empresa', resultado['chave_acesso'],
        motivo=MOTIVO_SERVICO_NAO_PRESTADO,
        justificativa='Reembolso solicitado pelo cliente dentro do prazo de garantia.',
        confirm=True,
    )
```

`operations.emit` refuses a reference already `emitida`, `incerta`, `preparada` or `cancelada`
(`DuplicateReferenceError`), marks `incerta` on `EmissionUncertainError` and never retries.
`operations.register_existing` and `operations.discard_reference` resolve those states.

## PDF

```python
from nfseai.pdf import render_nota_pdf

with EmissorNacionalClient.from_profile() as client:
    render_nota_pdf(client, chave, '/caminho/nota.pdf')     # needs the [pdf] extra and `playwright install chromium`
```

## Exceptions

All derive from `nfseai.NfseError`.

| Exception | Meaning |
|---|---|
| `NotConnectedError` (an `NfseAuthError`) | no credentials for the company |
| `NfseAuthError` | login refused or session expired with no retry allowed |
| `NfseHttpError` | network failure or unexpected HTTP status (`status_code`, `method`, `url`) |
| `NfseValidationError` | local validation or the portal refused the data (`errors`) |
| `EmissionRejectedError` | the portal answered the emission with an error; no invoice |
| `EmissionUncertainError` | emission sent, result unknown (`draft_id`, `final_url`, `debug_path`); never retry |
| `ConfirmationRequired` | a fiscal act was called without `confirm=True` |
| `UnsupportedScenario` | outside the mapped fiscal scenario |
| `PortalChangedError` | the portal form differs from the mapped contract |
| `CancellationNotAllowed` | already cancelled, or only fiscal analysis is available (`tipo_evento`) |
| `CaptchaRequired` | XML/DANFSe download, protected by hCaptcha |
| `DuplicateReferenceError` | the reference is already in the ledger (`status`, `chave_acesso`) |
| `FiscalProfileIncomplete` (a `ProfileError`) | fiscal defaults missing (`missing`) |
| `PdfUnavailableError` | Playwright/Chromium missing or the print page is unavailable |

## Rules for integrations

- Serialize emissions per company: one worker, one session. The portal session is a cookie; parallel
  emissions for the same CNPJ were not tested.
- Keep your own sale-to-invoice record as well, or use the ledger as that record and back it up.
- Rate: the client waits at least 0.35 s between requests and makes one login attempt per call.
- There is no sandbox on the portal: every emission is real. Test with `prepare`/rehearsal.
