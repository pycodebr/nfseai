# Changelog

Todas as versões publicadas do nfseai. O formato segue o [versionamento semântico](https://semver.org/lang/pt-BR/).

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
