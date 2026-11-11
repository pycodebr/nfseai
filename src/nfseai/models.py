"""Modelos de dados do cliente."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .values import parse_money

MOTIVO_ERRO_EMISSAO = '1'
MOTIVO_SERVICO_NAO_PRESTADO = '2'
MOTIVO_OUTROS = '9'

EVENTO_CANCELAMENTO = 101101
EVENTO_ANALISE_FISCAL = 101103
SECAO_CANCELAMENTO = 'Evento de Cancelamento de NFS-e'
SITUACAO_GERADA = 'P100_GERADA'
SITUACAO_CANCELADA = 'P104_NFSE_CANCELADA'


@dataclass(frozen=True)
class Endereco:
    cep: str
    codigo_municipio: str
    nome_municipio: str
    bairro: str
    logradouro: str
    numero: str
    complemento: str = ''


@dataclass(frozen=True)
class Tomador:
    """Tomador do serviço domiciliado no Brasil.

    `nome` é usado apenas quando a consulta do portal à RFB não devolve o nome.
    Para CNPJ, o endereço cadastrado na RFB é usado quando `endereco` é None e
    `usar_endereco_rfb` é True (mesmo comportamento da tela).
    """

    documento: str
    nome: str = ''
    email: str = ''
    telefone: str = ''
    endereco: Endereco | None = None
    usar_endereco_rfb: bool = True


@dataclass(frozen=True)
class Servico:
    """Serviço prestado.

    `codigo_tributacao_nacional` usa o formato do portal (ex.: '08.02.01'); `codigo_nbs`
    tem 9 dígitos. Os dois vêm do enquadramento fiscal da empresa: confirme com o
    contador. Sem `codigo_municipio_prestacao`, o serviço é prestado no município do
    emitente.
    """

    codigo_tributacao_nacional: str
    codigo_nbs: str
    descricao: str
    codigo_municipio_prestacao: str = ''
    nome_municipio_prestacao: str = ''
    informacoes_complementares: str = ''


@dataclass(frozen=True)
class Tributacao:
    """Opções de tributação mapeadas (Simples Nacional, sem retenção).

    `tipo_valor_tributos` é a pergunta "valor aproximado dos tributos" da tela:
    4 informa a alíquota do Simples Nacional (`aliquota_sn`, em %) e 3 não informa.
    """

    tipo_valor_tributos: int = 4
    aliquota_sn: Decimal | None = None
    regime_apuracao_sn: int = 1
    reter_issqn: bool = False
    preencher_ibs_cbs: bool = False


@dataclass(frozen=True)
class EmissionRequest:
    """Pedido de emissão. `referencia` é o identificador da venda ou do contrato no seu
    sistema: ele não vai para a nota, mas é a chave do registro local contra nota em dobro."""

    competencia: date
    tomador: Tomador
    valor: Decimal
    servico: Servico
    tributacao: Tributacao
    referencia: str = ''


@dataclass(frozen=True)
class PreparedEmission:
    """Rascunho completo no portal, pronto para emitir, com o resumo conferido."""

    draft_id: str
    request: EmissionRequest
    tomador_nome: str
    resumo: dict

    @property
    def valor_servico(self):
        return parse_money((self.resumo.get('Valores do Serviço Prestado') or {}).get(
            'Valor da operação/serviço prestado'
        ))

    @property
    def valor_liquido(self):
        return parse_money((self.resumo.get('Valor líquido da NFS-e') or {}).get('Valor líquido da NFS-e'))


@dataclass(frozen=True)
class NotaResumo:
    chave_acesso: str
    row_key: str
    data_geracao: str
    tomador_documento: str
    tomador_nome: str
    competencia: str
    municipio: str
    valor: Decimal | None
    situacao_codigo: str
    situacao: str
    pode_cancelar: bool
    pode_substituir: bool

    @property
    def cancelada(self):
        return self.situacao_codigo == SITUACAO_CANCELADA


@dataclass(frozen=True)
class NotasPage:
    notas: list
    total: int
    pagina: int
    ultima_pagina: int


@dataclass(frozen=True)
class NotaDetalhe:
    chave_acesso: str
    row_key: str
    secoes: dict

    def campo(self, secao, rotulo, default=''):
        return (self.secoes.get(secao) or {}).get(rotulo, default)

    @property
    def cancelamento(self):
        """Dados do evento de cancelamento (autor, data, motivo, justificativa) ou dict vazio."""
        return self.secoes.get(SECAO_CANCELAMENTO) or {}

    @property
    def cancelada(self):
        return SECAO_CANCELAMENTO in self.secoes

    @property
    def situacao(self):
        """Situação atual. O campo original da nota continua "100 - NFS-e Gerada" depois do
        cancelamento; o que muda é a presença do evento de cancelamento."""
        if self.cancelada:
            return 'NFS-e cancelada'
        return self.campo('Outras Informações', 'Situação da NFS-e')

    @property
    def valor_servico(self):
        return parse_money(self.campo('Tributação Municipal', 'Valor do Serviço'))

    @property
    def numero_nfse(self):
        """Número da NFS-e, que ocupa 13 posições da chave de acesso depois da inscrição do emitente."""
        return str(int(self.chave_acesso[23:36]))

    @property
    def data_geracao(self):
        return self.campo('Identificação da NFS-e', 'Data de geração')

    @property
    def competencia(self):
        """Competência exibida na página da nota, quando o portal a mostra; senão, texto vazio."""
        for fields in self.secoes.values():
            for rotulo, valor in fields.items():
                if 'competência' in rotulo.lower():
                    return valor
        return ''

    @property
    def numero_dps(self):
        return self.campo('Identificação do DPS', 'Número')

    @property
    def serie_dps(self):
        return self.campo('Identificação do DPS', 'Série')

    @property
    def tomador_documento(self):
        tomador = self.secoes.get('Tomador') or {}
        value = tomador.get('CPF') or tomador.get('CNPJ') or ''
        return ''.join(ch for ch in value if ch.isdigit())

    @property
    def tomador_nome(self):
        return self.campo('Tomador', 'Nome/Razão Social')


@dataclass(frozen=True)
class EmissionResult:
    chave_acesso: str
    draft_id: str
    final_url: str
    nota: NotaDetalhe | None = None


@dataclass(frozen=True)
class Rascunho:
    id: str
    tipo: str
    atualizado_em: str
    competencia: str
    emitida_para: str
    valor: Decimal | None
    passo: str
    editar_url: str


@dataclass(frozen=True)
class CancelPreview:
    chave_acesso: str
    tipo_evento: int
    mensagem: str
    form_action: str
    fields: dict = field(repr=False)
    motivos: dict = field(default_factory=dict)

    @property
    def cancelamento_direto(self):
        return self.tipo_evento == EVENTO_CANCELAMENTO


@dataclass(frozen=True)
class CancelResult:
    chave_acesso: str
    tipo_evento: int
    sucesso: bool
    mensagem: str
    situacao_apos: str = ''
