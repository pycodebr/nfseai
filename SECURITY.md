# Segurança

## Relatar uma falha

Se você encontrou uma falha que pode expor credenciais, dados fiscais ou permitir a emissão ou o cancelamento
de notas sem confirmação, **não abra uma issue pública**. Use o relato privado do GitHub:
[Security > Report a vulnerability](https://github.com/pycodebr/nfseai/security/advisories/new).

Inclua a versão (`nfseai --version`), o sistema operacional, os passos para reproduzir e o impacto. Não envie
senhas, CNPJ, CPF ou chaves de acesso de produção no relato.

## Modelo de segurança

- **Credenciais.** O CNPJ e a senha do portal ficam em `~/.nfseai/credentials.json`, em texto, com a pasta em
  modo 700 e o arquivo em modo 600. Quem tem acesso à sua conta de usuário no sistema operacional (ou é
  administrador da máquina) consegue ler o arquivo. Em servidores, prefira as variáveis de ambiente
  `NFSEAI_USER` e `NFSEAI_PASSWORD` vindas do cofre de segredos da plataforma.
- **Destino dos dados.** O programa só se conecta a `www.nfse.gov.br`. Não há servidor do projeto, telemetria
  ou envio de dados a terceiros. A instalação baixa pacotes do PyPI, o código do GitHub e, se você pedir o
  suporte a PDF, o Chromium distribuído pelo Playwright.
- **Agente de IA.** O agente executa o comando `nfseai` com as permissões do seu usuário e lê a saída, que
  inclui dados dos seus clientes. A senha não aparece na saída de nenhum comando, e o assistente
  `nfseai conectar` recusa rodar sem um terminal interativo. A exigência de confirmação antes de emitir é uma
  instrução da skill somada à opção `--confirm`: ela reduz o risco de uma emissão indevida, mas depende de o
  agente seguir as instruções. Revise as permissões que o seu agente tem para rodar comandos.
- **Atos fiscais.** Emissão e cancelamento exigem `--confirm` (`confirm=True` na biblioteca). Uma emissão
  nunca é reenviada automaticamente.
- **Captcha.** O projeto não contorna o captcha que protege o XML e o DANFSe oficial no portal.

## Versões com correção de segurança

Somente a versão mais recente recebe correções.
