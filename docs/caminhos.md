# Caminhos para emitir NFS-e no Sistema Nacional por software

Existem quatro formas de um sistema emitir NFS-e no padrão nacional. O `nfseai` implementa a segunda. Esta
página explica o motivo e os limites dessa escolha, para você decidir se ela serve ao seu caso.

## A. API oficial com certificado digital

A Sefin Nacional publica uma API (emissão, consulta, eventos e ADN) que recebe a DPS em XML assinada com
certificado ICP-Brasil, por conexão mTLS, e devolve o XML da NFS-e autorizada. A lista oficial de APIs está em
[gov.br/nfse](https://www.gov.br/nfse).

- Vantagens: é o canal de integração oficial, devolve o XML fiscal, tem ambiente de testes (produção
  restrita) e idempotência nativa, porque a série e o número da DPS são definidos por quem emite.
- Custos: certificado e-CNPJ A1 (compra, renovação anual e guarda segura do arquivo), assinatura XML,
  implementação do leiaute e das regras de validação, numeração própria e geração do PDF.

O `nfseai` **não implementa** este caminho. Para um volume alto de notas ou para uma operação que precisa
do XML de cada nota, ele é o destino recomendado.

## B. Portal do contribuinte por HTTP (o que o `nfseai` faz)

O Emissor Nacional (`www.nfse.gov.br/EmissorNacional`) é o portal em que a empresa entra com CNPJ e senha e
preenche a nota em quatro telas. O `nfseai` faz o mesmo login e envia as mesmas requisições que essas telas
enviam, sem navegador.

- Vantagens: funciona com o login que a empresa já tem, sem certificado; produz exatamente a nota que a tela
  produziria; cobre consulta, emissão e cancelamento; cada emissão usa cerca de 15 requisições leves.
- Limites:
  - Não é um canal de integração oficial. O portal pode mudar o formulário ou adotar barreiras contra
    automação. A verificação de contrato transforma mudança de formulário em erro antes do envio, mas alguém
    precisa remapear o passo ([remapeamento.md](remapeamento.md)).
  - O XML e o DANFSe oficial exigem hCaptcha no portal. O `nfseai` não contorna o captcha: ele entrega os
    dados da nota e o PDF da versão para impressão.
  - Não há ambiente de testes. O que existe é o ensaio: o rascunho é montado, conferido e descartado sem
    emitir.
  - Não há idempotência nativa. O controle contra nota em dobro é o registro local por referência, com a
    busca por tomador e valor no portal como segunda barreira.
  - Só o cenário fiscal mapeado é aceito (veja o README, "O que é suportado").

## C. Automação de navegador

Um robô que clica nas telas do portal. Tem a mesma dependência das telas que o caminho B, com mais lentidão
e mais pontos de falha (máscaras, componentes de seleção, tempo de carregamento). No `nfseai`, o navegador
aparece em dois lugares: para gerar o PDF e como ferramenta de mapeamento (`tools/capture.py`).

## D. Emissor privado com API

Empresas de emissão oferecem uma API própria sobre a API oficial e cuidam de certificado ou procuração,
assinatura, numeração e PDF. Troca manutenção por custo recorrente e por um fornecedor com acesso à emissão
fiscal da empresa.

## Como escolher

| Situação | Caminho |
|---|---|
| Poucas notas por mês, empresa do Simples (ME/EPP), sem certificado, quer pedir a nota a um agente de IA | B (`nfseai`) |
| Precisa do XML de cada nota, de ambiente de testes ou de alto volume | A |
| Empresa fora do cenário mapeado (MEI, fora do Simples, retenções, tomador no exterior) | A, D ou o portal à mão |
| Município que ainda não aderiu ao emissor nacional | sistema do município |

O contrato de dados do `nfseai` (`EmissionRequest` na entrada, chave de acesso na saída) foi desenhado para
aceitar um segundo transporte no futuro. Uma contribuição que implemente o caminho A é bem-vinda.
