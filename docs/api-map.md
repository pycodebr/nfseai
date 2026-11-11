# Mapa de rotas do Emissor Nacional (portal do contribuinte)

Mapeado em 09/11/2026, versão do portal 1.6.0.0, com login por CNPJ e senha em contas de empresas optantes do Simples Nacional (ME/EPP). Base: `https://www.nfse.gov.br/EmissorNacional`. Os caminhos não diferenciam maiúsculas de minúsculas.

Este mapa descreve o que o `nfseai` usa e o que foi apenas lido. Ele não é documentação oficial: o portal pode mudar sem aviso, e a verificação de contrato do cliente (`src/nfseai/contracts.py`) existe para transformar essa mudança em erro antes de qualquer envio.

O portal é ASP.NET MVC renderizado no servidor. A autenticação fica em cookies (`ASP.NET_SessionId`, `Emissor`), não há token de API utilizável e a sessão expira com 20 minutos de inatividade (`window.sessionTimeout = 1200`). Uma requisição com sessão expirada recebe `302` para `/Login?ReturnUrl=...` e não é processada.

Legenda: **validado** = executado contra o portal em produção; **mapeado** = lido do JavaScript e do HTML do portal, ainda não executado.

## Autenticação

| Método | Rota | Dados | Resposta | Situação |
|---|---|---|---|---|
| GET | `/Login?ReturnUrl=/EmissorNacional` | | HTML com `__RequestVerificationToken` | validado |
| POST | `/Login?ReturnUrl=/EmissorNacional` | `__RequestVerificationToken`, `Inscricao`, `Senha` | `302` para `/EmissorNacional` em caso de sucesso; `200` com mensagens de validação em caso de recusa | validado |
| GET | `/Login/Sair` | | encerra a sessão | mapeado |

O login por senha não pede captcha. O portal também oferece login por certificado digital (`certificado.nfse.gov.br`) e por gov.br, não usados aqui.

## Consultas de notas

| Método | Rota | Parâmetros | Resposta | Situação |
|---|---|---|---|---|
| GET | `/Notas/Emitidas` | `datainicio`, `datafim` (DD/MM/AAAA), `busca`, `pg`, `col` | tabela HTML, 15 linhas por página | validado |
| GET | `/Notas/Recebidas` | os mesmos | tabela HTML | validado |
| GET | `/Notas/Visualizar/Index/{chaveAcesso}` | | detalhe da NFS-e em painéis rótulo/valor | validado |
| GET | `/Notas/Visualizar/Impressao?chave={rowKey}` | | HTML da versão para impressão | validado |
| GET | `/Notas/Download/NFSe/{chaveAcesso}` | | `403` sem hCaptcha | validado (bloqueio) |
| GET | `/Notas/Download/DANFSe/{chaveAcesso}` | | `403` sem hCaptcha | validado (bloqueio) |
| GET | `/DPS/ModalCaptcha/Abrir/?redirectUrl=...` | | formulário com hCaptcha que libera o download | validado (leitura) |

Observações da listagem:

- O filtro de datas usa a data de geração e aceita no máximo 30 dias. Sem datas, o portal mostra os últimos 30 dias. Janela maior devolve "Nenhum registro encontrado".
- `busca` pesquisa pelo nome do tomador. CPF, chave de acesso e número da nota não retornam resultado.
- Cada linha (`tr`) traz `data-chave` (identificador interno opaco, aqui chamado `rowKey`), `data-situacao` (`P100_GERADA` para nota ativa) e `data-valor`. A chave de acesso de 50 dígitos está no link "Visualizar".
- A presença dos botões `.btnCancelar` e `.btnSubstituir` indica as ações disponíveis para a nota.
- O `rowKey` de uma nota também aparece no link "Imprimir" da página de detalhe, o que permite chegar nele a partir da chave de acesso.

## Rascunhos

| Método | Rota | Dados | Resposta | Situação |
|---|---|---|---|---|
| GET | `/Notas/Rascunhos` | | tabela HTML; cada linha tem `data-id` (o `idr`) | validado |
| POST | `/Rascunhos/Excluir/` | `id` | exclui o rascunho | validado |

## Emissão completa

A emissão tem quatro telas: Pessoas → Serviço → Valores → Emitir NFS-e. Cada POST grava o rascunho no servidor e devolve `302` para a tela seguinte com o identificador `idr`. Os formulários desses passos não usam token antifalsificação. Em caso de erro de validação, o portal devolve `200` com a mesma tela e as mensagens.

| Passo | Método | Rota | Resposta de sucesso | Situação |
|---|---|---|---|---|
| Pessoas | GET | `/DPS/Pessoas` | formulário | validado |
| Pessoas | POST | `/DPS/Pessoas` | `302` → `/DPS/Servico?idr={idr}` | validado |
| Serviço | GET | `/DPS/Servico?idr={idr}` | formulário | validado |
| Serviço | POST | `/DPS/Servico?idr={idr}` | `302` → `/DPS/Tributacao?idr={idr}` | validado |
| Valores | GET | `/DPS/Tributacao?idr={idr}` | formulário | validado |
| Valores | POST | `/DPS/Tributacao?idr={idr}` | `302` → `/DPS/EmitirNFSe?idr={idr}` | validado |
| Conferência | GET | `/DPS/EmitirNFSe?idr={idr}` | resumo da DPS e prévia dos valores | validado |
| Emissão | GET | `/DPS/NFSe/Index?idr={idr}` | gera a NFS-e e devolve `200` com a página de sucesso | validado |

A resposta da emissão vem na própria requisição, sem redirecionamento: um alerta `.alert-success` com "A NFS-e foi gerada com sucesso" e uma lista `dl` com a chave de acesso, o município gerador, a competência, o emitente, o tomador, o serviço e os valores. A página traz ainda os botões "Baixar XML" e "Baixar DANFSe" (os dois passam pelo hCaptcha) e "Visualizar NFS-e". O rascunho deixa de existir depois da emissão.

A nota emitida por este caminho sai com série de DPS `70000`, número sequencial próprio dessa série, versão da aplicação `EmissorWeb_1.6.0.0` e ambiente gerador "2 - Sefin Nacional NFS-e". A chave de acesso tem 50 dígitos: município (7), ambiente gerador (1), tipo de inscrição (1), inscrição do emitente (14), número da NFS-e (13), ano e mês (4), código (9) e dígito verificador (1).

O botão "Emitir NFS-e" (`#btnProsseguir`, `value="DPS"`) apenas navega para `/DPS/NFSe/Index?idr=...`. No login por certificado o valor é `AssinaturaDps` e a emissão passa pelo Assinador Serpro (`AssinaturaDPS/ObterXmlDPS`, `AssinaturaDPS/ProcessarDPS`, `DPS/NFSe/FinalizarAssinaturaNFSe`); esse modo não é suportado. A emissão não pede captcha.

Outras modalidades: `/DPS/Simplificada` responde "não permitido" para contas ME/EPP (é a emissão do MEI) e `/ByPass/Pessoas` é a emissão por decisão administrativa ou judicial. Nenhuma das duas foi mapeada.

### Consultas usadas pelas telas (JSON, somente leitura)

Todas em `/api/emissaodps/`. As oito primeiras foram validadas; `Cep` e a última linha foram apenas lidas no JavaScript do portal.

| Rota | Parâmetros | Uso |
|---|---|---|
| `ListarMunicipiosParaEmissao` | `tpInsc`, `insc`, `dtCompet` (DD/MM/AAAA) | município emissor do prestador |
| `recuperaropcaosn` | `tpInsc`, `insc`, `dtCompet` | opção no Simples Nacional (`Tipo` 3 = ME/EPP) |
| `ListarEstabelecimentosParaEmissao` | `tpInsc`, `insc`, `codMun`, `dtCompet` | estabelecimentos (lista vazia nas contas mapeadas) |
| `RecuperarInfoEstabelecimento` | `tpInsc`, `insc`, `codMun`, `inscMun`, `dtCompet` | cadastro do prestador (`InfCad`) |
| `RecuperarInfoInscricao/{cpf}?data=AAAA-MM-DD` | | nome do tomador pessoa física na RFB |
| `RecuperarInfoPessoaJuridicaTomador/{cnpj}?data=AAAA-MM-DD` | | nome e endereço do tomador pessoa jurídica |
| `BuscarNomeMunicipio` | `term`, `q`, `_type=query` | busca de município por nome (`id` = código IBGE) |
| `BuscarNomeServico` | `term`, `q`, `_type=query` | busca do código de tributação nacional |
| `VerificarIncidencia` | `cMunEmi`, `cMunTom`, `cMunPre`, `cTribNac`, `cTribMun`, `dtCompet` (AAAA-MM-DD), `cEhOptanteMei` | incidência do ISSQN e situação do convênio do município |
| `Cep/{cep}` | | endereço por CEP (envia `Authorization` com o `accessToken` da página) |
| `RecuperarListaDeIndOp`, `RecuperarListaDeCClassTrib`, `RecuperarInfoCClassTrib`, `recuperarbeneficio*`, `verificarretencoes` | | IBS/CBS, benefícios e retenções (mapeados, não usados) |

### Campos enviados em cada passo

A lista completa, na ordem do navegador, está em `src/nfseai/contracts.py` e os corpos capturados no navegador (com dados fictícios) em `tests/fixtures/captured_posts.json`. Os campos que variam por nota:

**Pessoas** (71 campos com endereço do tomador, 70 sem)

| Campo | Valor |
|---|---|
| `PreencherInfoIBSCBS` | `0` (não) |
| `DataCompetencia` | DD/MM/AAAA |
| `TipoEmitente` | `1` (prestador) |
| `Prestador.*` | cadastro devolvido por `RecuperarInfoEstabelecimento` e `ListarMunicipiosParaEmissao` |
| `SimplesNacional.Opcao` | `Tipo` de `recuperaropcaosn` |
| `SimplesNacional.RegimeApuracaoTributosSN` | `1` (tributos federais e municipal pelo Simples Nacional) |
| `Tomador.LocalDomicilio` | `1` (Brasil) |
| `Tomador.Inscricao` | CPF ou CNPJ formatado |
| `Tomador.Nome` | nome devolvido pela consulta à RFB |
| `Tomador.InformarEndereco` e `Tomador.EnderecoNacional.*` | enviados só quando há endereço (para CNPJ a tela preenche com o endereço da RFB) |
| `Intermediario.LocalDomicilio` | `0` (não informado) |

**Serviço** (68 campos)

| Campo | Valor |
|---|---|
| `LocalPrestacao.CodigoPaisPrestacao` | `BR` |
| `LocalPrestacao.CodigoMunicipioPrestacao` e `...DescricaoMunicipioPrestacao` | código IBGE e nome |
| `ServicoPrestado.CodigoTributacaoNacional` e `...DescricaoCodigoTributacaoNacional` | o código (ex.: `08.02.01`) e o texto de `BuscarNomeServico` |
| `ServicoPrestado.HaExportacaoImunidadeNaoIncidencia` | `0` |
| `MunicipioIncidencia.*` | resultado de `VerificarIncidencia` |
| `ServicoPrestado.CodigoNBS` | código NBS de 9 dígitos, entre as opções da tela (ex.: `122051900`) |
| `ServicoPrestado.Descricao` | até 2000 caracteres |
| `Complemento.InformacoesComplementares` | opcional, até 2000 caracteres |

**Valores** (60 campos)

| Campo | Valor |
|---|---|
| `Valores.ValorServico` | formato `1.047,51` |
| `ISSQN.TributacaoISSQN` | `1` (operação tributável) |
| `ISSQN.HaRetencao` | `0` |
| `ValorTributos.TipoValorTributos` | `4` (informar alíquota do Simples Nacional) ou `3` (não informar) |
| `ValorTributos.AliquotaSN` | alíquota em %, formato `3,00` |

## Cancelamento

| Método | Rota | Dados | Resposta | Situação |
|---|---|---|---|---|
| POST | `/Notas/Emitidas/ModalCancelamento/` | `chave` = `rowKey` | JSON `{Sucesso, TipoEvento, Mensagem, Html}`; não altera a nota | validado |
| POST | `/Notas/Emitidas/CancelarNFSe` | campos ocultos do formulário devolvido + `MotivoCancelamento` + `Justificativa` | JSON `{"TipoEvento":101101,"Processado":true,"Sucesso":true,"Chave":"<rowKey>","AssinarPedRegEvento":false}` | validado |
| POST | `/Notas/Emitidas/VerificarSubstituicao/` | `chave`, `bypass` | JSON `{Sucesso, Url, Mensagem}` (início da substituição) | mapeado |

- `TipoEvento` 101101 é o cancelamento direto. 101103 significa que a nota saiu do prazo ou caiu em alguma regra, e o portal só aceita um pedido de análise fiscal ao município.
- Motivos: `1` erro na emissão, `2` serviço não prestado, `9` outros. A justificativa aceita até 255 caracteres no portal; o cliente exige no mínimo 15 por precaução (regra local, não confirmada no portal).
- O formulário de cancelamento tem `__RequestVerificationToken` e campos ocultos com os dados da nota (`Chave`, `ChaveAcesso`, `NumeroDPS`, `SerieDPS`, `NumeroNFSe`, `ValorServico`, `EmitidoPara`, `ServicoPrestado`, `DataGeracao`).
- `AssinarPedRegEvento` na resposta indica que o portal exige assinatura do evento com certificado (login por certificado). Com login por senha, o esperado é `Processado`.
- O portal ofereceu cancelamento direto também para notas geradas pelo sistema próprio do município.
- Depois do cancelamento, a linha da listagem passa a ter `data-situacao="P104_NFSE_CANCELADA"` e perde os botões de cancelar e substituir. A página de detalhe ganha o painel "Evento de Cancelamento de NFS-e" (autor, data, código e texto da justificativa), mas o campo "Situação da NFS-e" continua "100 - NFS-e Gerada", porque ele vem do documento original. O cliente considera a nota cancelada pela presença do evento.
- `ModalCancelamento` continua respondendo com sucesso para uma nota já cancelada. O cliente consulta a nota antes e recusa o segundo cancelamento.

## Perfil

| Método | Rota | Uso | Situação |
|---|---|---|---|
| GET/POST | `/Perfil/Configuracao` | e-mail, telefone e opção padrão do valor aproximado dos tributos | mapeado (leitura) |
| GET | `/Perfil/ServicosFavoritos` | serviços favoritos (até 10) | mapeado (leitura) |
| POST | `/ModalProcuracoes/*` | procuração e representação | mapeado |

## Pontos em aberto

1. Cancelamento fora do prazo (evento 101103, pedido de análise fiscal): só a prévia foi lida no JavaScript.
2. Informar série e número da DPS (`InformarSerieNumeroDPS`), que daria um controle de duplicidade no próprio portal.
3. Prazo de cancelamento direto parametrizado pelo município.
4. Comportamento de uma segunda chamada a `/DPS/NFSe/Index` com o mesmo `idr`. Não foi testado, e o cliente nunca repete a chamada.
