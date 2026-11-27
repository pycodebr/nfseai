# Caminho A: API oficial da Sefin Nacional com certificado digital

Desenho do segundo transporte do `nfseai`, discutido na issue
[#2](https://github.com/pycodebr/nfseai/issues/2). O caminho B (portal do contribuinte por CNPJ e senha) continua
como está; o caminho A é escolhido por empresa e atende quem tem certificado e-CNPJ A1 e está fora do cenário do
portal mapeado: empresas fora do Simples Nacional e notas com retenção de ISSQN.

Legenda, a mesma de [api-map.md](api-map.md): **validado** = executado contra a Sefin Nacional (produção restrita
ou produção); **mapeado** = lido da documentação oficial, ainda não executado. Enquanto este documento não tiver
nenhuma linha validada, o caminho A não emite nota em produção.

## Fontes

Documentação técnica oficial, publicada em `gov.br/nfse` (Documentação Atual):

| Documento | Versão | Uso aqui |
|---|---|---|
| Esquemas XSD (`esquemas-xsd-v1.01-20260209.zip`) | 1.01 | DPS, NFS-e, pedido de registro de evento; copiados em `src/nfseai/schemas/1.01/` |
| Anexo I, leiaute e regras de negócio da DPS e da NFS-e | 1.01 (09/02/2026) | campos, faixas de série, códigos de rejeição |
| Anexo II, leiaute e regras dos eventos | 1.01 (22/01/2026) | cancelamento (evento 101101) |
| Manual dos Contribuintes, APIs do Emissor Público Nacional | 1.0 (17/03/2025) | rotas da Sefin Nacional |

O Swagger da Sefin Nacional (`sefin.nfse.gov.br/SefinNacional/docs/index`) só abre com certificado na conexão. O
formato JSON das mensagens (nomes dos campos do envio e do retorno) foi conferido nele na primeira chamada à
produção restrita (item 1 de "Pontos a confirmar").

## Rotas da Sefin Nacional

Bases: produção restrita `https://sefin.producaorestrita.nfse.gov.br/SefinNacional` (a confirmar: a página de
APIs do gov.br também mostra o caminho `/API/SefinNacional`) e produção `https://sefin.nfse.gov.br/SefinNacional`.
Toda chamada usa TLS mútuo com o certificado A1 do emitente.

| Método | Rota | O que faz | Situação |
|---|---|---|---|
| POST | `/nfse` | recebe a DPS assinada e devolve a NFS-e gerada ou os motivos da rejeição, de forma síncrona | validado (produção restrita e produção) |
| GET | `/nfse/{chaveAcesso}` | XML da NFS-e | validado |
| GET | `/dps/{id}` | chave de acesso da NFS-e gerada a partir de uma DPS, para quem consta na nota | validado (item 8) |
| HEAD | `/dps/{id}` | informa se a DPS já gerou NFS-e, sem devolver a chave | mapeado (o cliente usa o `GET`) |
| POST | `/nfse/{chaveAcesso}/eventos` | pedido de registro de evento assinado (cancelamento) | validado |
| GET | `/nfse/{chaveAcesso}/eventos/{tipoEvento}/{numSeqEvento}` | evento vinculado à nota | validado (item 7) |
| GET | `/parametros_municipais/{codigoMunicipio}/convenio` | convênio do município | mapeado |
| GET | `/parametros_municipais/{codigoMunicipio}/{codigoServico}` | alíquota, regimes especiais e deduções do serviço | mapeado |

## Por que o caminho A protege mais que o caminho B

O caminho B precisou do registro local para evitar nota em dobro porque o portal não tem idempotência. A API tem:
o identificador da DPS (`"DPS"` + município emissor + tipo de inscrição + CNPJ + série + número) é único, e uma
segunda DPS com o mesmo identificador é recusada com `E0014`. Com isso:

- o número da DPS é reservado no registro local **antes** do envio, e nunca é reaproveitado;
- quando o resultado de uma emissão fica incerto (queda de rede, tempo esgotado), `GET /dps/{id}` diz se a nota
  existe e devolve a chave, sem depender de procurar por tomador e valor;
- reenviar a mesma DPS não gera segunda nota, mas o cliente continua sem reenviar sozinho: a regra do projeto
  ("a emissão nunca é repetida automaticamente") vale igual.

O XML oficial da NFS-e vem na própria resposta da emissão, sem captcha.

## Como as proteções do projeto ficam no caminho A

| Proteção do caminho B | No caminho A |
|---|---|
| A senha não passa pelo chat | O arquivo `.pfx` e a senha do certificado entram só por `nfseai conectar --certificado`, no terminal do usuário, ou por variáveis de ambiente em servidor. A senha nunca aparece em saída, log, `repr` ou mensagem de erro |
| Ensaio antes de emitir | O ensaio monta a DPS, valida contra o XSD oficial, assina, confere os parâmetros do município e mostra o resumo. Nada é enviado à `/nfse` sem `--confirm` |
| Confirmação explícita | Igual: emitir e cancelar exigem `confirm=True` |
| Controle contra nota em dobro | Registro por referência (igual) somado ao identificador da DPS reservado antes do envio |
| Verificação do portal (contrato) | Validação da DPS contra o XSD empacotado antes de assinar; versão do leiaute fixada (`versao="1.01"`). Mudança de leiaute exige atualizar os esquemas, como hoje exige remapear o portal |
| Leitura posterior | A chave e o XML da resposta são conferidos: emitente, tomador, valor e competência da NFS-e devolvida precisam bater com o pedido |

## Arquitetura

O caminho A reaproveita os modelos (`EmissionRequest`, `Tomador`, `Servico`), o registro (`Ledger`), as operações
(`operations.emit`, `operations.cancel`) e a linha de comando. O que muda é o transporte, que expõe as mesmas
partes que `EmissorNacionalClient` expõe hoje (`emissao`, `notas`, `cancelamento`), para que `operations.py` não
precise saber qual caminho está em uso.

```
src/nfseai/
├── certificado.py   # lê o .pfx (A1), confere validade e o CNPJ do certificado (OID 2.16.76.1.3.3)
├── dps.py           # monta a DPS em XML a partir do EmissionRequest e valida contra o XSD
├── assinatura.py    # assinatura XMLDSig envelopada da DPS e do pedido de evento
├── sefin.py         # SefinSession (mTLS, HTTP/1.1, ritmo) e SefinClient (emissao, notas, cancelamento, dps)
└── schemas/1.01/    # XSD oficiais, sem alteração
```

- **Escolha do caminho por empresa.** Em `config.json`, cada empresa ganha `transporte` (`portal` ou `sefin`) e
  `ambiente` (`producao-restrita` ou `producao`). `EmissorNacionalClient.from_profile` devolve o cliente certo.
  Empresas já conectadas continuam em `portal` sem mudar nada.
- **Certificado.** O arquivo fica em `~/.nfseai/certificados/<empresa>.pfx` (modo 600) e a senha em
  `credentials.json`, ou ambos no ambiente (`NFSEAI_<EMPRESA>_CERT`, `NFSEAI_<EMPRESA>_CERT_PASSWORD`). Para o TLS
  mútuo, a chave é escrita em arquivo temporário com modo 600 na pasta privada e apagada ao fechar a sessão.
- **Dependências.** As bibliotecas de certificado e assinatura entram como extra opcional
  (`pip install 'nfseai[certificado]'`), no mesmo modelo do suporte a PDF. Quem usa só o caminho B não instala
  nada novo.
- **Numeração da DPS.** Uma tabela nova no `ledger.sqlite3` guarda o último número por empresa e série. A série
  padrão é `00001`, dentro da faixa oficial de aplicativo próprio (00001 a 49999). Na produção, a empresa pode
  já ter usado essa série em outro sistema (ERP, emissor anterior), e a Sefin recusaria os números repetidos com
  `E0014`. Por isso a documentação pede uma série exclusiva do nfseai antes da produção, e
  `nfseai configurar --serie N` troca a série sem reconectar o certificado. Pelo mesmo motivo, o `ledger.sqlite3`
  precisa ficar em armazenamento persistente quando o nfseai roda em servidor ou contêiner.

## Cenário inicial do caminho A

O mesmo princípio do caminho B: só o que for validado com nota real é aceito, e o resto é recusado com
`UnsupportedScenario` antes de qualquer envio.

| Aspecto | Cenário inicial |
|---|---|
| Emitente | não optante do Simples Nacional (`opSimpNac = 1`), sem regime especial (`regEspTrib = 0`) |
| Tomador | no Brasil, CPF ou CNPJ, com endereço nacional (obrigatório quando há retenção, regra E0237) |
| ISSQN | operação tributável, alíquota informada, sem retenção (`tpRetISSQN = 1`) ou retido pelo tomador (`tpRetISSQN = 2`) |
| Tributos federais | PIS/COFINS com CST e retenções de IRRF, CSLL e CP informadas pelo emitente |
| Total aproximado dos tributos | valor ou percentual informado, ou a indicação de não informar |
| Fora do cenário | MEI, regimes especiais, deduções e reduções, benefício municipal, IBS/CBS, obra, evento, exportação, intermediário, substituição |

O que cada empresa informa (códigos, alíquotas, retenções) vem do contador. O cliente não escolhe valor fiscal.

## Pontos a confirmar na produção restrita

Situação em 26/11/2026, depois dos primeiros envios à produção restrita com o certificado A1 de uma empresa
real, fora do Simples Nacional:

1. **Validado.** Nomes dos campos JSON: conferidos no Swagger da Sefin (`/SefinNacional/swagger/docs/v1`, que
   abre com o certificado). A resposta de erro dos eventos (`ResponseErro`) traz `erro`, um objeto, e não a
   lista `erros`.
2. **Validado.** Assinatura (C14N 1.0, enveloped, SHA-1, RSA-SHA1): a DPS foi aceita e a NFS-e gerada
   (cStat 100), com a assinatura da DPS e a da Sefin no XML devolvido. O pedido de cancelamento, assinado do
   mesmo jeito, também foi aceito.
3. **Validado.** Base `https://sefin.producaorestrita.nfse.gov.br/SefinNacional`, conexão TLS mútua aceita.
4. **Validado.** O grupo IBS/CBS continua opcional no leiaute da NT 009 (regra 596 do Anexo I da produção
   restrita só limita a data de competência).
5. **Em aberto.** Parâmetros municipais: no Swagger da Sefin, `/ParametrosMunicipais` diz que o serviço foi
   movido para `adn.producaorestrita.nfse.gov.br/parametrizacao`; as rotas do manual não respondem lá (404).
   Eles dizem, por exemplo, se o tomador tem o dever de reter o ISSQN (recusa E0657).
6. **Validado.** Cadeia do certificado do servidor: a lista de autoridades do sistema basta.
7. **Validado.** A consulta de evento é `GET /nfse/{chave}/eventos/{tipoEvento}/{numSeqEvento}`; o
   cancelamento é consultado em `/eventos/101101/1`. Nota sem cancelamento: "gerada"; depois do evento:
   "cancelada".
8. **Validado.** `GET /dps/{id}` recebe os 42 números do identificador, sem o prefixo `DPS` (com ele: E2400).
9. **Validado na produção.** Em novembro de 2026, notas emitidas na produção (`sefin.nfse.gov.br`) com o certificado de
   uma empresa fora do Simples Nacional, no leiaute publicado em fevereiro (o padrão do ambiente), com a série
   escolhida para o nfseai.

Ciclo validado na produção restrita: ensaio, emissão (com IRRF retido e com ISSQN retido pelo tomador),
consulta pela chave, XML oficial, consulta pela DPS, prévia e cancelamento.

A parametrização de retenção do ISSQN da produção restrita pode diferir da produção. No município testado, a
produção restrita exigiu retenção pelo tomador (E0657, regra 554 do Anexo I) para um serviço cuja nota é aceita
sem retenção na produção. Para testar, informe a retenção que o ambiente exige; na produção, siga o contador.

## Detalhes já resolvidos na implementação

- **Defeito no XSD oficial.** O padrão da série em `tiposSimples_v1.01.xsd` é `^0{0,4}\d{1,5}$`. Em XML Schema,
  `^` e `$` são caracteres literais, e o libxml2 recusa qualquer série. A correção é feita só em memória ao
  carregar o esquema (`dps.carregar_schema`); os arquivos em `schemas/` ficam iguais aos publicados.
- **Canonicalização de subárvore no libxml2.** Ao canonicalizar em memória um nó dentro de um documento em que o
  namespace padrão muda (a `Signature` dentro da DPS), o libxml2 acrescenta `xmlns=""` aos filhos, e a
  assinatura não confere em nenhum verificador correto. `assinatura._c14n` canonicaliza cada nó como documento
  à parte. Os testes conferem toda assinatura com uma biblioteca independente (`signxml`).
- **Certificado exportado pelo Windows.** O `.pfx` exportado pelo `certmgr` vem codificado em BER, não em DER. A
  biblioteca `cryptography` abre e avisa que versões futuras podem recusar. O aviso é silenciado na abertura (ele
  sujaria a saída JSON); se a recusa vier, a saída é converter o arquivo para DER ao conectar a empresa.
- **Dois leiautes da versão 1.01.** Desde 20/10/2026 a produção restrita aplica o leiaute da Nota Técnica 009
  (reforma tributária; pacote de esquemas de 24/11/2026, em `schemas/1.01-nt009/`) e recusa a DPS do pacote
  publicado em 09/02/2026 com E1235. A produção segue no pacote de fevereiro até a virada anunciada pelo
  CGNFS-e. O leiaute é escolhido pelo ambiente; `nfseai configurar --leiaute 1.01-nt009` (ou
  `Store.save_sefin_options(empresa, leiaute=...)`) força um deles para a virada. Na DPS da NT 009 entram `finNFSe` = 0 (nota regular) depois de `tpEmit`
  e `indDest` = 0 (o destinatário é o tomador) depois de `toma`, e o `cNBS` passa para antes da descrição. O grupo IBS/CBS continua
  opcional e não é informado. Os eventos (cancelamento) não mudaram entre os pacotes.
- **Declaração UTF-8.** A Sefin recusa o XML sem `<?xml version="1.0" encoding="UTF-8"?>` (E1229).
- **IRRF retido pelo tomador.** `configurar --aliquota-irrf 1,5` grava a alíquota; a emissão calcula o valor
  truncando nos centavos (R$ 1.234,50 x 1,5% = 18,5175 -> R$ 18,51) e
  informa `tribFed/vRetIRRF`. A Sefin exige valor maior que zero e menor que o valor do serviço (RN 525, E0700) e
  sempre o desconta do líquido (RN 388). Valor igual ou inferior a R$ 10,00 não é retido (Lei 9.430/1996,
  art. 67). `--sem-irrf` emite uma nota sem a retenção. CP, CSLL e PIS/COFINS
  retidos continuam fora do cenário; o grupo `piscofins` não é informado.
- **NBS opcional.** O grupo `cNBS` é opcional na DPS 1.01 e notas reais de serviço sem NBS são aceitas; no
  caminho A o NBS não é obrigatório nos padrões fiscais. O caminho B continua exigindo, como o portal.
- **Campos JSON num só lugar.** Os nomes dos campos do envio e do retorno ficam em `sefin.CAMPOS`, para que a
  conferência no Swagger mude uma linha, não o cliente.
- **Compatibilidade com `operations.py`.** `SefinClient` expõe `emissao`, `notas` e `cancelamento` com os
  mesmos métodos do caminho B, e `operations.emit` roda sobre ele sem alteração (teste
  `test_operations_do_projeto_funcionam_com_o_cliente_da_sefin`).

## Etapas

1. Certificado, montagem da DPS, validação no XSD e assinatura, com testes sem rede e certificado fictício gerado
   no próprio teste. **Feita.**
2. `SefinSession` e `SefinClient`: emissão, consulta por chave e por DPS, cancelamento, com sessão falsa nos testes.
   **Feita.**
3. Perfis, `nfseai conectar --certificado`, numeração no registro, linha de comando e skill. **Feita.**
4. **Feita.** Validação em produção restrita com o certificado de uma empresa real; este documento passa a separar o que foi
   validado.
5. Pull request.
6. **Feita.** Notas emitidas na produção (item 9 de "Pontos a confirmar").
7. Ajustes depois da produção: troca de série e de leiaute pela linha de comando, orientação nas recusas E0014
   e E1235 e roteiro de ida para a produção em `skills/nfseai/references/certificado.md`.
