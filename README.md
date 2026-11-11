<div align="center">

# 🧾 nfseai

**Emissão de NFS-e pelo Emissor Nacional para agentes de IA.**
Um plugin com skill, linha de comando e biblioteca Python que permite ao seu agente consultar, emitir e cancelar notas fiscais de serviço no portal nacional (`www.nfse.gov.br`) e gerar o PDF de cada nota, usando o login da sua própria empresa.

[![CI](https://github.com/pycodebr/nfseai/actions/workflows/ci.yml/badge.svg)](https://github.com/pycodebr/nfseai/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-blue)](skills/nfseai/SKILL.md)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-✓-black)](#instalação)
[![Codex](https://img.shields.io/badge/Codex-✓-black)](#instalação)
[![OpenCode](https://img.shields.io/badge/OpenCode-✓-black)](#instalação)
[![Gemini CLI](https://img.shields.io/badge/Gemini%20CLI-✓-black)](#instalação)
[![Antigravity](https://img.shields.io/badge/Antigravity-✓-black)](#instalação)
[![Hermes](https://img.shields.io/badge/Hermes-✓-black)](#instalação)

Feito pela [PycodeBR](https://github.com/pycodebr) · Felipe Azambuja · [English below](#english)

</div>

---

## O que é

O **nfseai** ensina o seu agente de IA a trabalhar com a Nota Fiscal de Serviço eletrônica (NFS-e) no padrão nacional. Depois de instalado, você pede em linguagem comum:

> "Emita uma nota de R$ 1.500,00 para o CNPJ 11.444.777/0001-61, referente ao pedido 1042."
> "Quais notas eu emiti em outubro?"
> "Cancele a nota do pedido 1042, o cliente desistiu do serviço."
> "Me mande o PDF da última nota."

O agente monta a nota no portal, mostra o resumo para você conferir, emite somente depois da sua confirmação e devolve o número e a chave de acesso.

O projeto tem três partes, que funcionam juntas ou separadas:

| Parte | Para que serve |
|---|---|
| **Skill** (`skills/nfseai/`) | As instruções que o agente lê: como conectar a conta, como emitir com segurança, o que fazer quando algo falha |
| **Linha de comando** (`nfseai`) | O programa que conversa com o portal. O agente usa por baixo, e você também pode usar direto no terminal |
| **Biblioteca Python** (`import nfseai`) | Para chamar a emissão de dentro do seu próprio sistema |

Ele funciona com qualquer empresa que emita pelo Emissor Nacional dentro do [cenário suportado](#o-que-é-suportado). Os dados fiscais (código do serviço, NBS, descrição e alíquota) são da sua empresa e ficam configurados na sua máquina.

## Como funciona

```text
Você ──pede a nota──▶ Agente de IA ──lê──▶ Skill nfseai
                          │
                          └──roda──▶ nfseai (CLI) ──HTTPS──▶ www.nfse.gov.br/EmissorNacional
                                        │
                                        └── ~/.nfseai  (login, padrões fiscais, registro das emissões)
```

O Emissor Nacional é o portal em que a empresa entra com CNPJ e senha e preenche a nota em quatro telas. O `nfseai` faz o mesmo login e envia as mesmas requisições que essas telas enviam, sem abrir navegador. Não é necessário certificado digital.

Como nota fiscal é documento fiscal, o projeto foi construído em torno de cinco proteções:

1. **A senha não passa pelo chat.** Você digita o CNPJ e a senha no seu terminal, com `nfseai conectar`. O agente não consegue rodar esse comando no seu lugar.
2. **Ensaio antes de emitir.** Sem `--confirm`, o comando monta o rascunho no portal, confere o resumo e descarta o rascunho. Nada é emitido.
3. **Confirmação explícita.** Emitir e cancelar exigem `--confirm`, e a skill instrui o agente a pedir a sua autorização para cada nota depois de mostrar o resumo.
4. **Controle contra nota em dobro.** Cada emissão é registrada por referência (o número do pedido ou da venda no seu sistema). A mesma referência não gera duas notas, e o comando ainda procura no portal uma nota ativa com o mesmo cliente e valor.
5. **Verificação do portal.** Se o formulário do portal mudar, a emissão para com um erro antes de enviar qualquer dado, em vez de arriscar uma nota errada.

## Instalação

### Um comando para todos os agentes (recomendado)

```bash
curl -fsSL https://raw.githubusercontent.com/pycodebr/nfseai/main/install.sh | bash
```

O instalador:

- detecta os agentes presentes na máquina (Claude Code, Codex, OpenCode, Gemini CLI, Antigravity, Cursor e Hermes) e instala a skill em cada um;
- cria um ambiente Python isolado em `~/.nfseai/venv`, sem tocar no Python do sistema;
- instala o comando `nfseai` em `~/.nfseai/bin` e cria o atalho em `~/.local/bin`;
- confere a instalação e o acesso ao portal com `nfseai doctor`.

Requisitos: Linux ou macOS (no Windows, use o WSL2), `git` e Python 3.10 ou mais novo. Se a máquina não tiver Python, o instalador baixa o [uv](https://docs.astral.sh/uv/), que traz o próprio Python; nesse caso é preciso ter o `curl`.

Opções: `--agent claude|codex|opencode|gemini|agy|cursor|hermes|all` instala para um agente específico, `--pdf` já instala o suporte a PDF, `--copy` copia a skill em vez de criar um link, `--no-setup` instala só a skill e `--uninstall` remove a skill dos agentes sem apagar os seus dados.

```bash
curl -fsSL https://raw.githubusercontent.com/pycodebr/nfseai/main/install.sh | bash -s -- --agent claude --pdf
```

### Instalação nativa de cada agente

Se você prefere o gerenciador de plugins do próprio agente, use o comando dele. Nesse caminho o agente recebe a skill, e o comando `nfseai` é instalado na primeira vez que a skill for usada (o agente roda o `scripts/setup.sh` da skill).

| Agente | Comando |
|---|---|
| **Claude Code** | `claude plugin marketplace add pycodebr/nfseai && claude plugin install nfseai@nfseai` |
| **Codex** | `codex plugin marketplace add pycodebr/nfseai && codex plugin add nfseai@nfseai` |
| **Gemini CLI** | `gemini extensions install https://github.com/pycodebr/nfseai` |
| **Antigravity (agy)** | `git clone https://github.com/pycodebr/nfseai ~/.nfseai/src && agy plugin install ~/.nfseai/src` |
| **Hermes** | `curl -fsSL https://raw.githubusercontent.com/pycodebr/nfseai/main/install.sh \| bash -s -- --agent hermes` |
| **OpenCode** | `npx skills add pycodebr/nfseai -g -a opencode` |
| **Qualquer outro agente com Agent Skills** | `npx skills add pycodebr/nfseai -g` |

Dentro do Claude Code, os mesmos passos são `/plugin marketplace add pycodebr/nfseai` e `/plugin install nfseai@nfseai`.

No Hermes, o caminho indicado é o instalador. O repositório também é um pacote Agent Plugins v1 válido (`hermes plugins validate` aprova), mas a verificação de segurança que o Hermes aplica a fontes da comunidade bloqueia a instalação pelo gerenciador de plugins, porque a documentação e o `setup.sh` contêm comandos de instalação (`pip install`, `git clone`). O instalador coloca a skill em `~/.hermes/skills/productivity/nfseai`, sem passar por esse bloqueio.

### Instalação manual

```bash
git clone https://github.com/pycodebr/nfseai.git
cd nfseai && bash install.sh              # ou copie skills/nfseai para a pasta de skills do seu agente
```

Para usar só a linha de comando ou a biblioteca, sem agente:

```bash
pip install "nfseai @ git+https://github.com/pycodebr/nfseai"
```

## Primeiro uso

### 1. Conecte a sua conta do portal

No **seu terminal** (não no chat do agente), rode:

```bash
nfseai conectar
```

```text
nfseai · conectar ao Emissor Nacional de NFS-e (www.nfse.gov.br)

Apelido desta empresa (letras minúsculas, números e "-") [principal]: minha-empresa
CNPJ (ou CPF) do login no portal: 11.222.333/0001-81
Senha do portal (não aparece enquanto você digita):

Testando o login no portal (uma única tentativa, por precaução contra bloqueio da conta)...

Login confirmado: EMPRESA EXEMPLO LTDA (11.222.333/0001-81).
Empresa "minha-empresa" conectada. Credenciais gravadas em ~/.nfseai/credentials.json (modo 600).
```

O CNPJ e a senha são os mesmos que você usa em <https://www.nfse.gov.br/EmissorNacional>. Se a sua empresa nunca entrou no portal, crie a senha antes pelo link "Fazer primeiro acesso" da tela de login.

Se você pedir uma nota ao agente antes de conectar, ele mesmo vai orientar esse passo e esperar você terminar.

### 2. Informe os dados fiscais das suas notas

Logo depois de conectar, o assistente pergunta os padrões que valem para todas as notas da empresa:

| Dado | Exemplo | Onde encontrar |
|---|---|---|
| Código de tributação nacional do serviço | `08.02.01` | Com o seu contador, ou pesquisando no assistente por uma palavra ("treinamento", "software") |
| Código NBS | `122051900` | Com o seu contador |
| Descrição padrão do serviço | "Desenvolvimento de software sob encomenda." | Você define |
| Alíquota do Simples Nacional | `6,00` | Com o seu contador (ou escolha não informar o valor aproximado dos tributos) |

Você pode refazer esse passo com `nfseai configurar`, ou pedir ao agente que grave os valores: eles não são segredo. Qualquer um deles também pode ser informado em uma nota específica, sem mudar o padrão.

Se a sua empresa já emitiu alguma nota pelo portal, o código do serviço, o NBS e a descrição aparecem nela: use `nfseai emitidas` para achar a chave e `nfseai nota <chave>` para ver a seção "Serviço Prestado".

### 3. Confira

```bash
nfseai status --online
```

A resposta mostra a empresa conectada, o nome lido do portal e se os padrões fiscais estão completos. O campo `proximo_passo` diz o que falta.

### 4. Faça um ensaio

```bash
nfseai preparar --documento 11.444.777/0001-61 --valor 10,00
```

O ensaio percorre as telas do portal com os seus dados, mostra o resumo da nota e descarta o rascunho. Se ele termina sem erro, a sua empresa está dentro do cenário suportado e pronta para emitir.

## Como usar com o agente

Converse normalmente. Um exemplo de como a conversa acontece:

```text
Você:    Emita uma nota de R$ 1.500,00 para o CNPJ 11.444.777/0001-61, pedido 1042.

Agente:  Fiz o ensaio no portal e a nota ficaria assim:
         • Tomador: TOMADORA EXEMPLO LTDA (11.444.777/0001-61)
         • Valor do serviço: R$ 1.500,00
         • Competência: 10/11/2026
         • Serviço: 08.02.01 · Treinamento prestado conforme contrato.
         Nada foi emitido ainda. Posso emitir?

Você:    Pode.

Agente:  Nota emitida.
         • NFS-e nº 27
         • Chave de acesso: 41999992211222333000181… (50 dígitos)
         • Situação: NFS-e Gerada
         • PDF: ~/.nfseai/pdf/NFSe_27_....pdf (versão para impressão do portal)
```

Outros pedidos que a skill cobre:

- "Liste as notas emitidas em outubro e some o valor."
- "Emita as notas destes cinco pedidos" (o agente ensaia todas, mostra uma tabela e emite uma por vez depois da sua confirmação).
- "A nota do pedido 1042 saiu com o valor errado: cancele e emita de novo com R$ 1.450,00."
- "Conecte também a minha segunda empresa."

## Como usar pelo terminal

```bash
nfseai status                                   # conectada? padrões fiscais completos?
nfseai emitidas --inicio 01/10/2026 --fim 31/10/2026 --todas
nfseai nota <chave>                             # detalhe de uma nota

# emissão: primeiro o ensaio, depois o mesmo comando com --confirm
nfseai emitir --documento 11.444.777/0001-61 --valor 1500,00 --referencia pedido-1042
nfseai emitir --documento 11.444.777/0001-61 --valor 1500,00 --referencia pedido-1042 --confirm --pdf

# cancelamento: sem --confirm mostra a prévia
nfseai cancelar <chave> --motivo nao-prestado --justificativa "Serviço não prestado: pedido cancelado pelo cliente."
nfseai cancelar <chave> --motivo nao-prestado --justificativa "Serviço não prestado: pedido cancelado pelo cliente." --confirm

nfseai pdf <chave> --out ~/notas/nota-1042.pdf  # precisa do suporte a PDF: nfseai instalar-pdf
nfseai registro --referencia pedido-1042        # o que o registro local sabe sobre o pedido
```

Todos os comandos respondem em JSON. A lista completa está em [`comandos.md`](skills/nfseai/references/comandos.md).

| Comando | O que faz |
|---|---|
| `conectar`, `desconectar`, `status`, `empresas`, `usar` | Conexão das empresas com o portal |
| `configurar` | Padrões fiscais das notas |
| `emitidas`, `recebidas`, `nota`, `rascunhos` | Consultas |
| `servicos`, `municipios` | Pesquisa de código de serviço e de código IBGE |
| `preparar`, `emitir` | Ensaio e emissão |
| `cancelar-previa`, `cancelar` | Cancelamento |
| `pdf`, `impressao` | PDF e HTML da versão para impressão |
| `registro`, `registrar`, `registro-descartar` | Registro local das emissões |
| `doctor`, `instalar-pdf` | Conferência da instalação e suporte a PDF |

### Mais de uma empresa

```bash
nfseai conectar --empresa filial-sul     # no seu terminal
nfseai empresas                          # lista os apelidos e mostra a empresa padrão
nfseai usar filial-sul                   # troca a padrão
nfseai --empresa matriz emitidas         # um comando para uma empresa específica
```

### Servidores e contêineres

Onde não há terminal interativo, as credenciais vêm de variáveis de ambiente, que você define no cofre de segredos ou no arquivo de ambiente do serviço:

```bash
NFSEAI_USER=11222333000181
NFSEAI_PASSWORD=...
# ou, por empresa: NFSEAI_<APELIDO>_USER e NFSEAI_<APELIDO>_PASSWORD
```

Detalhes em [`credenciais.md`](skills/nfseai/references/credenciais.md).

## O que é suportado

Validado no portal em produção: login, listagem de notas emitidas e recebidas, detalhe da nota, rascunhos, emissão completa, cancelamento direto e PDF.

A emissão cobre este cenário fiscal:

| Aspecto | Suportado |
|---|---|
| Emitente | Empresa optante do Simples Nacional como ME ou EPP, com tributos recolhidos pelo Simples |
| Município | Um município de emissão, conveniado ao Sistema Nacional NFS-e |
| Tomador | No Brasil, pessoa física (CPF) ou jurídica (CNPJ) |
| Serviço | Qualquer código de tributação nacional e NBS aceitos pelo portal, operação tributável |
| Retenção de ISSQN | Sem retenção |
| Login | CNPJ e senha do portal |

Ainda **não** são suportados: MEI (emissão simplificada), empresas fora do Simples Nacional, retenção de ISSQN e deduções, informações de IBS/CBS, tomador no exterior, intermediário, exportação, obras e eventos, CNPJ com mais de um município de emissão, substituição de nota, login por certificado digital ou gov.br, e download do XML e do DANFSe oficial (os dois ficam atrás de captcha no portal, e o projeto não contorna captcha).

Quando a nota ou a empresa está fora do cenário, o comando recusa com `UnsupportedScenario` antes de enviar qualquer dado. A tabela completa, com o que foi validado e o que foi apenas implementado, está em [`cenarios.md`](skills/nfseai/references/cenarios.md). Se o seu caso não está coberto, veja [`docs/caminhos.md`](docs/caminhos.md) para as alternativas e [`docs/remapeamento.md`](docs/remapeamento.md) para contribuir com o mapeamento.

## Segurança e privacidade

- **Onde ficam os seus dados.** Tudo fica em `~/.nfseai`, com acesso restrito ao seu usuário do sistema (pasta em modo 700 e arquivos em modo 600): `credentials.json` (CNPJ e senha), `config.json` (padrões fiscais), `sessions/` (sessão do portal), `ledger.sqlite3` (registro das emissões), `debug/` (respostas do portal nas emissões) e `pdf/`.
- **Como a senha é guardada.** Em texto, protegida pelas permissões do arquivo, como acontece com `~/.netrc` e `~/.aws/credentials`. Ela é enviada somente para `www.nfse.gov.br`, no login. O projeto não tem servidor próprio, telemetria ou qualquer envio de dados a terceiros.
- **O que o agente vê.** O agente lê a saída dos comandos, que inclui nome e CPF ou CNPJ dos seus clientes. A skill instrui o agente a não copiar esses dados para canais compartilhados, logs ou memória. A senha nunca aparece na saída de nenhum comando.
- **Atos fiscais.** Emitir e cancelar exigem `--confirm`. A emissão nunca é repetida automaticamente: quando o resultado fica incerto, a referência fica bloqueada até alguém conferir no portal.
- **Remoção.** `nfseai desconectar --confirm` apaga a senha e a sessão de uma empresa. Para apagar tudo, remova a pasta `~/.nfseai`.

Para relatar uma falha de segurança, veja [SECURITY.md](SECURITY.md).

## Uso como biblioteca Python

```python
from datetime import date
from decimal import Decimal

from nfseai import EmissionRequest, EmissorNacionalClient, Ledger, Store, Tomador, operations

store = Store()                                    # ~/.nfseai
ledger = Ledger(store.ledger_path)
fiscal = store.fiscal('minha-empresa')             # padrões gravados com `nfseai configurar`

with EmissorNacionalClient.from_profile('minha-empresa') as client:
    pedido = EmissionRequest(
        competencia=date.today(),
        tomador=Tomador(documento='11.444.777/0001-61'),
        valor=Decimal('1500.00'),
        servico=fiscal.servico(),
        tributacao=fiscal.tributacao(),
        referencia='pedido-1042',
    )
    ensaio = operations.emit(client, ledger, 'minha-empresa', pedido)                   # nada é emitido
    resultado = operations.emit(client, ledger, 'minha-empresa', pedido, confirm=True)  # emissão
    print(resultado['numero_nfse'], resultado['chave_acesso'])
```

Referência completa em [`python-api.md`](skills/nfseai/references/python-api.md).

## Perguntas frequentes

**Isso é uma API oficial?**
Não. O canal oficial de integração é a API da Sefin Nacional, que exige certificado digital. O `nfseai` usa o portal do contribuinte com o seu login, do mesmo jeito que você usaria no navegador. A comparação entre os caminhos está em [`docs/caminhos.md`](docs/caminhos.md).

**Preciso de certificado digital?**
Não. Bastam o CNPJ e a senha do Emissor Nacional.

**Sou MEI. Funciona?**
Ainda não. O MEI usa a emissão simplificada, que tem outra tela e não foi mapeada. As consultas funcionam.

**Dá para baixar o XML da nota?**
Não por aqui. O portal protege o XML e o DANFSe oficial com captcha, e o projeto não contorna captcha. O PDF gerado é a versão para impressão do portal, com todos os dados e a chave de acesso. O XML continua disponível no portal, em "NFS-e Emitidas".

**Existe ambiente de testes?**
O portal não tem. O que existe é o ensaio: o rascunho é montado e conferido no portal e depois descartado, sem emitir.

**O que acontece se o portal mudar?**
Antes de enviar cada passo, o cliente compara o formulário do portal com o formulário mapeado. Se houver campo novo ou removido, a emissão para com `PortalChangedError`. Atualize o `nfseai` (rode o instalador de novo) e, se o erro continuar, abra uma issue com a mensagem.

**Meu município ainda usa sistema próprio. Funciona?**
O `nfseai` só conversa com o Emissor Nacional. Se o seu município ainda não aderiu ao emissor nacional, a emissão continua no sistema da prefeitura.

**Quanto tempo leva uma emissão?**
Nas nossas medições, o ensaio levou cerca de 20 segundos e a geração do PDF, cerca de 30 segundos. A emissão confirmada refaz a conferência, envia a nota e lê o resultado de volta, então conte com algo perto de um minuto por nota com PDF.

**Posso emitir várias notas de uma vez?**
Sim, uma depois da outra. O agente ensaia todas, mostra a lista e emite em sequência depois da sua confirmação.

## Estrutura do repositório

```text
nfseai/
├── install.sh                      # instalador para todos os agentes
├── skills/nfseai/
│   ├── SKILL.md                    # instruções que o agente lê
│   ├── references/                 # credenciais, emissão, cancelamento, comandos, cenários, problemas, Python
│   └── scripts/setup.sh            # cria o ambiente e o comando nfseai
├── src/nfseai/                     # pacote Python: cliente do portal, CLI, perfis, registro, PDF
├── tests/                          # testes sem rede, com páginas do portal e dados fictícios
├── tools/                          # ferramentas para remapear o portal
├── docs/                           # mapa de rotas do portal, caminhos e remapeamento
├── plugin.json                     # Agent Plugins v1 (Hermes, Antigravity)
├── .claude-plugin/                 # plugin e marketplace do Claude Code
├── .codex-plugin/ · .agents/       # plugin e marketplace do Codex
└── gemini-extension.json           # extensão do Gemini CLI
```

## Desenvolvimento

```bash
git clone https://github.com/pycodebr/nfseai && cd nfseai
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest          # roda sem rede
.venv/bin/ruff check .
```

Os testes comparam, campo a campo, o que o cliente envia com o que o navegador envia em cada passo da emissão (`tests/fixtures/captured_posts.json`). As páginas de teste vieram do portal e tiveram os dados trocados por valores fictícios.

Contribuições são bem-vindas, em especial o mapeamento de novos cenários fiscais. Veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Aviso

Este é um projeto independente e de código aberto. Ele não tem vínculo com a Receita Federal, com o Comitê Gestor da NFS-e, com o Serpro ou com qualquer prefeitura, e não é um canal oficial de integração. O portal pode mudar sem aviso.

A responsabilidade pelas notas emitidas e canceladas é de quem as emite. Confira os dados fiscais com o seu contador e confira as primeiras notas no portal. O software é fornecido sem garantia, nos termos da [licença MIT](LICENSE).

## Licença e créditos

- Código e documentação: [MIT](LICENSE) © PycodeBR (Felipe Azambuja).
- Dependências: [httpx](https://www.python-httpx.org/) (BSD-3-Clause), [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) (MIT), [lxml](https://lxml.de/) (BSD-3-Clause), [python-dotenv](https://github.com/theskumar/python-dotenv) (BSD-3-Clause) e, para o PDF, [Playwright](https://playwright.dev/python/) (Apache-2.0).
- Formato da skill: [Agent Skills](https://agentskills.io) (`SKILL.md`).

---

## English

**nfseai** is an open-source plugin (Agent Skill + CLI + Python library) that lets AI coding agents issue, query and cancel Brazilian service invoices (NFS-e) on the national portal **Emissor Nacional** (`www.nfse.gov.br`), and render each invoice as a PDF, using the company's own portal login. No digital certificate is needed.

- **One-command install for every agent:** `curl -fsSL https://raw.githubusercontent.com/pycodebr/nfseai/main/install.sh | bash` detects Claude Code, Codex, OpenCode, Gemini CLI, Antigravity, Cursor and Hermes, installs the skill and the `nfseai` command in an isolated environment. Native installs: `claude plugin marketplace add pycodebr/nfseai && claude plugin install nfseai@nfseai`, `codex plugin marketplace add pycodebr/nfseai && codex plugin add nfseai@nfseai`, `gemini extensions install https://github.com/pycodebr/nfseai`, `npx skills add pycodebr/nfseai -g`.
- **Credentials never go through the chat:** the user runs `nfseai conectar` in their own terminal; the password is read without echo, tested once and stored in `~/.nfseai` with mode 600. Servers use `NFSEAI_USER` / `NFSEAI_PASSWORD`.
- **Safe fiscal acts:** every emission is rehearsed first (the draft is built, checked and discarded), emitting and cancelling require `--confirm`, a local ledger keyed by the sale reference prevents duplicate invoices, an emission is never retried automatically, and a contract check stops the emission if the portal form changes.
- **Scope:** Simples Nacional companies (ME/EPP) with one issuing municipality, customers in Brazil, no ISSQN retention. MEI, companies outside Simples, foreign customers and the official XML/DANFSe download (behind captcha) are not supported.
- **Not an official integration channel.** It talks to the taxpayer portal with the user's login; the official API requires a digital certificate. The project is independent and not affiliated with any government body.

The skill and its references are written in English; commands, JSON keys and user-facing messages are in Portuguese. MIT © PycodeBR (Felipe Azambuja).
