# Connecting a company account

The portal has no API key or token for password login: the session is a cookie obtained with the company's
CNPJ (or CPF) and password. nfseai needs those two values on the machine where it runs. This file explains
how they get there without ever passing through you.

## The rule

The user types the password into `nfseai conectar`, in their own terminal. You never ask for it, never
receive it, never put it in a command, a file, a log or your memory. `nfseai conectar` checks that it is
attached to a real terminal and exits with code 2 otherwise, so running it from your tool shell does not
work, on purpose.

## What the user does

```text
$ nfseai conectar
Apelido desta empresa (letras minúsculas, números e "-") [principal]: minha-empresa
CNPJ (ou CPF) do login no portal: 11.222.333/0001-81
Senha do portal (não aparece enquanto você digita):
Testando o login no portal (uma única tentativa, por precaução contra bloqueio da conta)...
Login confirmado: EMPRESA EXEMPLO LTDA (11.222.333/0001-81).
Empresa "minha-empresa" conectada. Credenciais gravadas em ~/.nfseai/credentials.json (modo 600).
Quer gravar agora os padrões fiscais das notas (serviço, NBS, descrição, alíquota)? (S/n)
```

- The nickname (`apelido`) is how the company is called in every command (`--empresa minha-empresa`). The
  first company connected becomes the default one.
- The login is tested once. If the portal refuses it, nothing is saved and the wizard says so.
- `--sem-teste` saves without testing (offline machine). `--padrao` makes this company the default.
  `--sem-configurar` skips the fiscal defaults question.

The CNPJ and password are the same ones the user types at
`https://www.nfse.gov.br/EmissorNacional/Login`. If the user has never used the portal, they create the
password there first ("Fazer primeiro acesso"); nfseai does not create accounts. Login by digital certificate or
by gov.br is not supported.

## What you say

When `nfseai status` exits with 3, or a command fails with `NotConnectedError`:

> Para emitir notas eu preciso que você conecte a sua conta do Emissor Nacional uma única vez. Abra um
> terminal nesta máquina e rode `nfseai conectar`. Ele pergunta o CNPJ e a senha do portal (a senha não
> aparece na tela), testa o login e guarda tudo só no seu computador, em `~/.nfseai`. Não me envie a senha
> aqui no chat. Quando terminar, me avise.

If the command is not on the user's `PATH`, give the full path: `~/.nfseai/bin/nfseai conectar`.

When the user comes back, run `nfseai status --online`. It logs in and returns `portal.nome` and
`portal.inscricao`. Confirm with the user that this is the right company before any emission.

If the user pastes the password in the chat: do not use it and do not repeat it. Answer that the password
should not travel through the chat, ask them to run `nfseai conectar`, and suggest changing the password on
the portal, since it is now in the conversation history.

## Where things are stored

Everything lives in `~/.nfseai` (directory mode 700, files mode 600). `NFSEAI_HOME` moves it.

| Path | Content |
|---|---|
| `credentials.json` | CNPJ/CPF and password per company (plain text, readable only by the user's OS account) |
| `config.json` | default company and fiscal defaults per company (no secret) |
| `sessions/<empresa>.json` | portal session cookies (20 minutes of idle time on the portal side) |
| `ledger.sqlite3` | local ledger of emissions (reference, buyer document, amount, access key) |
| `debug/` | raw portal answers of emissions and cancellations, for diagnosis (contain fiscal data) |
| `pdf/` | PDFs generated without `--out` |

The password is stored in plain text protected by file permissions, the same model as `~/.netrc` or
`~/.aws/credentials`. It is sent only to `www.nfse.gov.br`. Never read `credentials.json` yourself, never
print it, never copy `~/.nfseai` into a repository or a backup you create.

## Several companies

```bash
nfseai conectar --empresa filial-sul       # the user, in their terminal
nfseai empresas                             # nicknames, which are connected, which is the default
nfseai usar filial-sul                      # change the default
nfseai --empresa matriz emitidas            # one command for a specific company
```

`--empresa` works before or after the command name. When several companies exist, say which one you are
about to use before a fiscal act, and confirm it with the user if there is any doubt.

## Servers, containers and chat-only agents

When nobody has an interactive terminal on the machine (a container, a CI job, an agent reached only through
a messaging app), the credentials come from environment variables that **the user or their operator**
sets in the service's secret store or env file:

| Variable | Use |
|---|---|
| `NFSEAI_USER`, `NFSEAI_PASSWORD` | login of the company in use |
| `NFSEAI_<EMPRESA>_USER`, `NFSEAI_<EMPRESA>_PASSWORD` | login of one specific company; `<EMPRESA>` is the nickname in upper case with `-` as `_` (`filial-sul` -> `NFSEAI_FILIAL_SUL_USER`) |
| `NFSEAI_EMPRESA` | nickname to use when `--empresa` is not passed |
| `NFSEAI_ENV_FILE` (or `--env-file`) | a dotenv file holding the variables above |
| `NFSEAI_HOME` | data directory instead of `~/.nfseai` |

The environment wins over `credentials.json`. `nfseai status` shows `origem_credenciais` as `ambiente` or
`arquivo`. You may tell the user which variables to set and where; you do not write the values.

## Disconnecting

```bash
nfseai desconectar --empresa minha-empresa --confirm    # deletes the saved password and session
```

Fiscal defaults and the ledger stay. To remove everything, the user deletes `~/.nfseai`.
