# Remapear o portal quando ele mudar (ou mapear um cenário novo)

O `nfseai` compara cada formulário do portal com um contrato capturado do tráfego do portal
(`src/nfseai/contracts.py`). Quando o portal muda, a emissão para com `PortalChangedError` e alguém precisa
capturar o passo de novo. O mesmo procedimento serve para mapear um cenário que ainda não é suportado (MEI,
retenção de ISSQN, tomador no exterior).

Você precisa de uma conta de produção do portal no cenário que vai mapear. Não existe ambiente de testes.

## Regras

1. **Nunca clique em "Emitir NFS-e" durante o mapeamento.** Pare na tela de conferência (`/DPS/EmitirNFSe`).
   O rascunho que você criou pode ser excluído depois com `nfseai rascunho-excluir <id> --confirm`.
2. Páginas e capturas do portal ficam em `.state/`, que está no `.gitignore`. Elas contêm CNPJ, nomes, endereços
   e tokens. Nada de `.state/` entra em commit, issue ou pull request.
3. As fixtures que vão para o repositório são geradas por `tools/build_fixtures.py`, que troca os dados reais
   por fictícios. Antes do commit, procure por dados reais nas fixtures.
4. Não afrouxe a verificação de contrato para "fazer passar". Campo novo no portal é sinal para entender o
   que ele significa antes de enviar qualquer valor.

## Preparação

```bash
git clone https://github.com/pycodebr/nfseai && cd nfseai
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,pdf]'
.venv/bin/python -m playwright install chromium
.venv/bin/nfseai conectar            # a sua conta, no seu terminal
```

## 1. Ler as páginas por HTTP

`tools/explore.py` faz GET autenticado, salva o HTML em `.state/pages/` e resume formulários, links e alertas.

```bash
.venv/bin/python tools/explore.py get /EmissorNacional --save home
.venv/bin/python tools/explore.py get /EmissorNacional/Notas/Emitidas --save emitidas --links
.venv/bin/python tools/explore.py get /EmissorNacional/Notas/Rascunhos --save rascunhos
.venv/bin/python tools/explore.py get /EmissorNacional/DPS/Pessoas --save form-step1
```

## 2. Capturar os POSTs no navegador

`tools/capture.py` abre o Chromium já logado (com os cookies da sessão do `nfseai`), e grava em
`.state/captures/<nome>.jsonl` cada requisição que a tela faz, com senha, token e cookie mascarados. Você
preenche o passo na tela como faria à mão e clica em "Avançar".

```bash
.venv/bin/nfseai whoami                                                        # renova a sessão
.venv/bin/python tools/capture.py step1 /EmissorNacional/DPS/Pessoas           # tomador com CNPJ
.venv/bin/python tools/capture.py step1cpf /EmissorNacional/DPS/Pessoas        # tomador com CPF
.venv/bin/python tools/capture.py step2 "/EmissorNacional/DPS/Servico?idr=<ID>" --save-page form-step2
.venv/bin/python tools/capture.py step3 "/EmissorNacional/DPS/Tributacao?idr=<ID>" --save-page form-step3
.venv/bin/python tools/capture.py step4 "/EmissorNacional/DPS/EmitirNFSe?idr=<ID>" --save-page conferencia
```

O `<ID>` é o `idr` que aparece na URL depois do primeiro "Avançar". Particularidades das telas:

- Os botões de opção (radio) ficam ocultos atrás de rótulos estilizados; clique no rótulo.
- As listas simples usam o plugin `chosen`; município e serviço usam `select2` com busca por AJAX.
- Os campos com máscara (CNPJ, valor, alíquota) precisam ser digitados tecla a tecla, sem colar.
- Ao sair do campo de data de competência e do campo de documento do tomador, a tela consulta a API interna
  (`/api/emissaodps/...`); essas chamadas aparecem na captura e mostram de onde vem cada valor do formulário.

## 3. Atualizar o contrato e o cliente

1. Em cada captura, a linha com `"method": "POST"` e `"type": "document"` traz `body_form`: a lista de pares
   campo/valor na ordem em que o navegador enviou. Essa ordem e os campos repetidos importam.
2. Atualize `src/nfseai/contracts.py`: `*_FIELDS` com os campos enviados, na ordem, e `*_KNOWN_UNSENT` com os
   campos que existem no HTML e não são enviados no cenário (seções ocultas ou desabilitadas).
3. Atualize `src/nfseai/client.py` (`_step_pessoas`, `_step_servico`, `_step_tributacao`) com a origem de cada
   valor novo. Valor fixo do cenário vai como constante; valor que caracteriza o cenário e vem em campo oculto
   entra em `_expect_hidden`, para o cliente recusar quando o portal indicar outro cenário.
4. Cenário novo: acrescente os campos ao modelo (`models.py`), libere a combinação em `_validate_request` e
   documente em `skills/nfseai/references/cenarios.md` e no README.

## 4. Regerar as fixtures e testar

```bash
cp tools/scrub.example.json tools/scrub.local.json      # preencha com os SEUS dados reais -> fictícios
.venv/bin/python tools/build_fixtures.py
grep -rn "<seu CNPJ>\|<sua razão social>\|<seu município>" tests/fixtures   # não pode achar nada
.venv/bin/python -m pytest && .venv/bin/ruff check .
```

`tests/test_emission.py` compara, campo a campo, o corpo que o cliente monta com o corpo capturado no
navegador (`tests/fixtures/captured_posts.json`). Se os dois batem para tomador com CNPJ e com CPF, o cliente
envia o que a tela enviaria.

## 5. Validar no portal, sem emitir

```bash
.venv/bin/nfseai preparar --documento <CPF ou CNPJ> --valor 10,00      # monta, confere e descarta
```

O ensaio percorre os três passos e a conferência no portal em produção. A primeira nota emitida depois de um
remapeamento deve ser uma nota que você precisa emitir de qualquer forma, conferida no portal em seguida.

## 6. Enviar a contribuição

Abra o pull request com o contrato, o cliente, as fixtures sem dados reais, os testes e a atualização do
mapa em [`api-map.md`](api-map.md), dizendo o que foi **validado** (executado contra o portal) e o que foi só
**mapeado** (lido do HTML ou do JavaScript).
