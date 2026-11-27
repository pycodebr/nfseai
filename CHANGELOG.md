# Changelog

Todas as versões publicadas do nfseai. O formato segue o [versionamento semântico](https://semver.org/lang/pt-BR/).

## Não publicado

- **Caminho A: API oficial da Sefin Nacional com certificado digital A1** ([#2](https://github.com/pycodebr/nfseai/issues/2)).
  Validado na produção restrita (emissão, consulta, XML, consulta pela DPS e cancelamento) e com notas emitidas na
  produção (`docs/caminho-a.md`). Atende emitentes fora do Simples Nacional,
  com ou sem ISSQN retido pelo tomador. `nfseai conectar --certificado` grava o certificado da empresa (a senha é
  digitada no terminal do usuário). O ensaio monta, valida no XSD oficial 1.01 e assina a DPS sem enviar nada; a
  emissão reserva o número da DPS no registro local e, se a conexão cair, consulta a DPS na Sefin antes de
  qualquer outra ação. Comandos novos: `xml` (XML oficial da nota) e `dps`. O suporte a certificado é um extra
  opcional (`nfseai[certificado]`); quem usa só o portal não instala nada novo. IRRF retido pelo tomador
  (`--aliquota-irrf`); o NBS é opcional nesse caminho.
- Caminho A: `nfseai configurar --serie N` troca a série da DPS sem reconectar o certificado, e
  `nfseai configurar --leiaute 1.01-nt009|1.01|padrao` força o leiaute para a virada da NT 009 sem precisar da
  biblioteca Python. As recusas E0014 (série e número já usados) e E1235 (leiaute diferente do que o ambiente
  aplica) trazem na mensagem o que fazer, e a exceção ganha `dicas`; a lista `errors` continua com o texto da
  Sefin. A documentação traz um roteiro de ida para a produção.
- O caminho B (portal) não mudou. `status` e `empresas` passam a informar o `caminho` de cada empresa.

## 0.1.1 (2026-11-10)

- `setup.sh` tenta a instalação dos pacotes uma segunda vez, sem cache, quando o download falha (um download
  interrompido aparece no pip como erro de hash), e termina com uma mensagem que diz como rodar de novo se a
  falha continuar. O problema apareceu em uma instalação de teste a partir do repositório público.
- `setup.sh` confere se a tag da versão existe antes de instalar pelo GitHub, em vez de tentar a tag e cair
  no `main` depois do erro.

## 0.1.0 (2026-11-10)

Primeira versão pública.

- **Cliente do Emissor Nacional** (`www.nfse.gov.br/EmissorNacional`) por HTTP, com login por CNPJ e senha:
  notas emitidas e recebidas, detalhe da nota, rascunhos, emissão completa (Pessoas, Serviço, Valores e
  conferência), cancelamento direto e versão para impressão. Emissão mapeada para empresas do Simples Nacional
  (ME/EPP) com tributos recolhidos pelo Simples, um município de emissão conveniado, tomador no Brasil e sem
  retenção de ISSQN. Fora desse cenário, o cliente recusa com `UnsupportedScenario` antes de enviar dados.
- **Verificação de contrato**: cada formulário do portal é comparado com o formulário mapeado; campo novo ou
  removido interrompe a emissão com `PortalChangedError`.
- **Linha de comando `nfseai`** com saída em JSON: `status`, `empresas`, `usar`, `configurar`, `whoami`,
  `servicos`, `municipios`, `emitidas`, `recebidas`, `nota`, `rascunhos`, `preparar`, `emitir`,
  `cancelar-previa`, `cancelar`, `pdf`, `impressao`, `registro`, `registrar`, `registro-descartar`, `doctor`
  e `instalar-pdf`.
- **Conexão segura da conta**: `nfseai conectar` é um assistente que o próprio usuário roda no terminal. A
  senha é lida sem eco, testada em uma única tentativa e gravada em `~/.nfseai/credentials.json` com modo 600.
  O comando recusa rodar sem terminal, para um agente não pedir a senha no chat. Servidores usam
  `NFSEAI_USER` e `NFSEAI_PASSWORD`.
- **Várias empresas** por apelido, cada uma com os próprios padrões fiscais (código de tributação nacional,
  NBS, descrição e alíquota do Simples), que também podem ser informados por nota.
- **Ensaio antes de emitir**: sem `--confirm`, `emitir` monta o rascunho no portal, confere o resumo e
  descarta o rascunho.
- **Registro local das emissões** por referência (`~/.nfseai/ledger.sqlite3`): a mesma referência não é
  emitida duas vezes, uma emissão com resultado incerto bloqueia a referência até a conferência, e a emissão
  nunca é repetida automaticamente.
- **PDF** da versão para impressão do portal (`nfseai pdf`), com suporte opcional instalado por
  `nfseai instalar-pdf`. O XML e o DANFSe oficial ficam atrás de captcha no portal e não são automatizados.
- **Skill para agentes** (`skills/nfseai`) com referências de credenciais, emissão, cancelamento, comandos,
  cenários, problemas e uso em Python.
- **Empacotamento como plugin**: instalador único (`install.sh`) para Claude Code, Codex, OpenCode,
  Gemini CLI, Antigravity, Cursor e Hermes; manifestos para o marketplace do Claude Code e do Codex, extensão
  do Gemini CLI e pacote Agent Plugins v1.
- **Ferramentas de mapeamento** (`tools/`) e guia para remapear o portal quando ele mudar.
