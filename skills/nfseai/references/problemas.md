# Troubleshooting

## Installation

| Symptom | Cause and fix |
|---|---|
| `nfseai: command not found` | Use `~/.nfseai/bin/nfseai`, or add `~/.local/bin` to `PATH`. Not installed at all: `bash <skill_dir>/scripts/setup.sh` |
| `setup.sh`: "é preciso ter Python 3.10+ (com venv) ou curl" | Install one of them: `sudo apt install python3 python3-venv` (Debian/Ubuntu), `brew install python` (macOS), or `curl` |
| `doctor`: `pasta de dados` not ok | `chmod 700 ~/.nfseai && chmod 600 ~/.nfseai/*.json ~/.nfseai/ledger.sqlite3` |
| `doctor`: `portal www.nfse.gov.br` not ok | No network, a proxy, or the portal is down. Try `https://www.nfse.gov.br/EmissorNacional/Login` in a browser |
| Windows | Use WSL2. The file-permission model (`chmod 600`) is POSIX |

## Login and session

| Symptom | Cause and fix |
|---|---|
| `NotConnectedError` | No credentials for the company: the user runs `nfseai conectar` |
| `NfseAuthError: login recusado pelo portal ... Usuário ou senha inválidos` | Wrong or changed password. The user checks it in the browser, then `nfseai conectar` again. Do not retry in a loop |
| `EmissionUncertainError: falha ao enviar a emissão ... sessão expirada no portal` | The session expired exactly at the emission request. A redirect to the login means the portal did not process it, but the client cannot prove that, so the reference is `incerta`: follow `emissao.md` § Uncertain result |
| `credenciais incompletas no ambiente: falta NFSEAI_...` | Only one of the two environment variables is set |
| `há mais de uma empresa configurada` | Pass `--empresa` or set the default with `nfseai usar <apelido>` |
| Wrong company answers | `nfseai status` shows `empresa` and `origem_credenciais`. Environment variables win over the file |

The client logs in again by itself when a session expires on a read. It never re-sends an emission or a
cancellation.

## Emission

| Symptom | Cause and fix |
|---|---|
| `FiscalProfileIncomplete` | Missing service code, NBS, description or tax estimate: `nfseai configurar ...` or pass them on the command |
| `NfseValidationError: código de tributação nacional ... não encontrado` | The service code does not exist on the portal: `nfseai servicos "<palavra>"` |
| `NfseValidationError: código NBS ... não existe no portal` | The NBS is not in the portal's list for the service screen. The user confirms the code with the accountant |
| `NfseValidationError: nome do tomador não encontrado na RFB nem informado` | The Receita Federal lookup returned no name for that document: check the document; as a last resort pass `--nome` |
| `NfseValidationError: o portal recusou o passo "..."` | The portal's own validation message follows. Report it literally |
| `NfseValidationError: conferência do rascunho ... falhou` | The portal's summary did not match the request (issuer, customer, amount or competência). Nothing was emitted; the draft was discarded. Report it; do not force |
| `NfseValidationError: a competência não pode ser futura` | Use a date up to today |
| `UnsupportedScenario` | The company or the invoice is outside the mapped scenario (`cenarios.md`) |
| `EmissionRejectedError` | The portal answered the emission with an error and no invoice. The message is the portal's. The reference becomes `erro` and can be tried again after fixing the cause |
| `EmissionUncertainError` | `emissao.md` § Uncertain result. Never retry |
| `DuplicateReferenceError` | `emissao.md` § The reference and the ledger |

## The portal changed

`PortalChangedError: formulário "<passo>" mudou no portal; campos ausentes: [...]; campos novos: [...]`

The portal's form no longer matches the contract the client was mapped against. Emitting with a different
form could put wrong data on a fiscal document, so the client stops before sending.

1. Stop emitting for this company. Queries and PDFs may still work.
2. Check for a newer version: `bash <skill_dir>/scripts/setup.sh` (or `git -C ~/.nfseai/src pull` and run
   setup again).
3. Still failing: tell the user the client needs an update and point to the issues page,
   `https://github.com/pycodebr/nfseai/issues`, with the step name and the two field lists from the message
   (they contain no personal data). The user can emit on the portal by hand meanwhile and attach the invoice
   to the reference with `nfseai registrar`.
4. Do not edit `contracts.py` to silence the error unless the user asked you to remap the portal as a
   development task; that procedure is `docs/remapeamento.md` in the repository.

The same applies to `PortalChangedError` from other pages ("identificação do contribuinte logado não
encontrada", "página da NFS-e sem chave de acesso reconhecível").

## PDF

| Symptom | Cause and fix |
|---|---|
| `PdfUnavailableError: o Playwright não está instalado` | `nfseai instalar-pdf` |
| `PdfUnavailableError: o navegador não conseguiu gerar o PDF: ... Host system is missing dependencies` | Linux without Chromium's system libraries: `~/.nfseai/venv/bin/python -m playwright install-deps chromium` (asks for sudo), then try again |
| The user wants the official DANFSe or the XML | Both are behind hCaptcha on the portal: the user downloads them in the browser ("NFS-e Emitidas"). The tool's PDF is the print version, with all the data and the access key |
| No PDF support wanted | `nfseai impressao <chave> --out nota.html` saves the print version as HTML |

## Permissions and privacy

- `~/.nfseai` must stay 700 and its files 600. Do not copy it into a project directory, a repository or a
  shared drive.
- `~/.nfseai/debug/` and `ledger.sqlite3` hold customers' documents and amounts. They are the user's fiscal
  records; do not upload them anywhere.
- When you report an error to the user or to an issue, remove CPF/CNPJ, names and access keys of third
  parties unless the user needs them.
