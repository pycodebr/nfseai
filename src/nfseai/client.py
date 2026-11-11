"""Cliente do Emissor Nacional de NFS-e (portal do contribuinte, login por CNPJ/senha).

Rotas e payloads foram mapeados a partir do tráfego real do portal; veja
docs/api-map.md. Operações que alteram estado fiscal (emitir, cancelar, excluir
rascunho) exigem `confirm=True` e fazem leitura posterior do resultado.
"""
import contextlib
import re
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from . import contracts
from .config import APP_PATH, Credentials, format_cep, format_document, only_digits, validate_document
from .errors import (
    CancellationNotAllowed,
    CaptchaRequired,
    ConfirmationRequired,
    EmissionRejectedError,
    EmissionUncertainError,
    NfseAuthError,
    NfseHttpError,
    NfseValidationError,
    PortalChangedError,
    UnsupportedScenario,
)
from .models import (
    EVENTO_CANCELAMENTO,
    SITUACAO_GERADA,
    CancelResult,
    EmissionResult,
    Endereco,
    PreparedEmission,
)
from .parsing import (
    CHAVE_RE,
    form_field_names,
    hidden_value,
    parse_cancel_modal,
    parse_conferencia,
    parse_emissao_resultado,
    parse_identity,
    parse_nota_detalhe,
    parse_notas,
    parse_rascunhos,
    parse_validation_errors,
    select_options,
    soup_of,
)
from .transport import PortalSession
from .values import format_money, parse_money

API = f'{APP_PATH}/api/emissaodps'
MAX_LIST_WINDOW_DAYS = 30


def _br_date(value):
    return value.strftime('%d/%m/%Y')


def _tipo_inscricao(digits):
    return 'CNPJ' if len(digits) == 14 else 'CPF'


class Consultas:
    """Consultas auxiliares que a própria tela de emissão faz (somente leitura)."""

    def __init__(self, session):
        self._session = session

    def municipios_para_emissao(self, inscricao, competencia):
        return self._session.get_json(
            f'{API}/ListarMunicipiosParaEmissao',
            params={'tpInsc': _tipo_inscricao(inscricao), 'insc': inscricao, 'dtCompet': _br_date(competencia)},
        )

    def opcao_simples(self, inscricao, competencia):
        return self._session.get_json(
            f'{API}/recuperaropcaosn',
            params={'tpInsc': _tipo_inscricao(inscricao), 'insc': inscricao, 'dtCompet': _br_date(competencia)},
        )

    def estabelecimento(self, inscricao, codigo_municipio, competencia, inscricao_municipal=''):
        return self._session.get_json(
            f'{API}/RecuperarInfoEstabelecimento',
            params={
                'tpInsc': _tipo_inscricao(inscricao),
                'insc': inscricao,
                'codMun': codigo_municipio,
                'inscMun': inscricao_municipal,
                'dtCompet': _br_date(competencia),
            },
        )

    def tomador(self, documento, competencia):
        """Nome (e endereço, para CNPJ) do tomador nas bases da RFB consultadas pelo portal."""
        digits = validate_document(documento)
        route = 'RecuperarInfoPessoaJuridicaTomador' if len(digits) == 14 else 'RecuperarInfoInscricao'
        data = self._session.get_json(
            f'{APP_PATH}/api/EmissaoDPS/{route}/{digits}', params={'data': competencia.isoformat()}
        )
        data = data or {}
        endereco = None
        if data.get('cep') and data.get('codigoibgemunicipio'):
            endereco = Endereco(
                cep=only_digits(data.get('cep')),
                codigo_municipio=str(data.get('codigoibgemunicipio')),
                nome_municipio=(data.get('nomemunicipio') or '').upper(),
                bairro=data.get('bairro') or '',
                logradouro=data.get('logradouro') or '',
                numero=data.get('numero') or '',
                complemento=data.get('complemento') or '',
            )
        return {'documento': digits, 'nome': (data.get('nomerazaosocial') or '').strip(), 'endereco': endereco}

    def buscar_municipio(self, termo):
        params = {'term': termo, '_type': 'query', 'q': termo}
        return self._session.get_json(f'{API}/BuscarNomeMunicipio', params=params).get('results', [])

    def buscar_servico(self, termo):
        params = {'term': termo, '_type': 'query', 'q': termo}
        return self._session.get_json(f'{API}/BuscarNomeServico', params=params).get('results', [])

    def verificar_incidencia(self, *, municipio_emissor, municipio_tomador, municipio_prestacao,
                             codigo_tributacao_nacional, competencia, optante_mei=False):
        return self._session.get_json(
            f'{API}/VerificarIncidencia',
            params={
                'cMunEmi': municipio_emissor,
                'cMunTom': municipio_tomador,
                'cMunPre': municipio_prestacao,
                'cTribNac': codigo_tributacao_nacional,
                'cTribMun': '',
                'dtCompet': competencia.isoformat(),
                'cEhOptanteMei': 'true' if optante_mei else 'false',
            },
        )


class Notas:
    """Consulta de NFS-e emitidas e recebidas."""

    def __init__(self, session):
        self._session = session

    def _listar(self, rota, inicio, fim, busca, pagina):
        params = {}
        if (inicio is None) != (fim is None):
            raise ValueError('informe inicio e fim juntos')
        if inicio is not None:
            if fim < inicio:
                raise ValueError('fim anterior ao inicio')
            if (fim - inicio).days > MAX_LIST_WINDOW_DAYS:
                raise ValueError(f'o portal aceita janelas de até {MAX_LIST_WINDOW_DAYS} dias; use iter_emitidas')
            params.update({'datainicio': _br_date(inicio), 'datafim': _br_date(fim)})
        if busca:
            params['busca'] = busca
        if pagina > 1:
            params['pg'] = pagina
        response = self._session.get(f'{APP_PATH}/Notas/{rota}', params=params)
        return parse_notas(response.text, pagina=pagina)

    def emitidas(self, inicio=None, fim=None, *, busca=None, pagina=1):
        """Uma página (15 linhas) das notas emitidas.

        O filtro de datas usa a data de geração e aceita no máximo 30 dias; sem datas,
        o portal lista os últimos 30 dias. `busca` pesquisa pelo nome do tomador.
        """
        return self._listar('Emitidas', inicio, fim, busca, pagina)

    def recebidas(self, inicio=None, fim=None, *, busca=None, pagina=1):
        return self._listar('Recebidas', inicio, fim, busca, pagina)

    def iter_emitidas(self, inicio, fim, *, busca=None):
        """Percorre todas as notas emitidas do período, em janelas de 30 dias e todas as páginas."""
        cursor = inicio
        seen = set()
        while cursor <= fim:
            window_end = min(cursor + timedelta(days=MAX_LIST_WINDOW_DAYS), fim)
            pagina = 1
            while True:
                page = self.emitidas(cursor, window_end, busca=busca, pagina=pagina)
                for nota in page.notas:
                    if nota.chave_acesso not in seen:
                        seen.add(nota.chave_acesso)
                        yield nota
                if pagina >= page.ultima_pagina or not page.notas:
                    break
                pagina += 1
            cursor = window_end + timedelta(days=1)

    def get(self, chave_acesso):
        """Detalhe de uma NFS-e pela chave de acesso de 50 dígitos."""
        if not CHAVE_RE.fullmatch(chave_acesso or ''):
            raise NfseValidationError('chave de acesso deve ter 50 dígitos', ['chave inválida'])
        response = self._session.get(f'{APP_PATH}/Notas/Visualizar/Index/{chave_acesso}')
        nota = parse_nota_detalhe(response.text)
        if nota.chave_acesso != chave_acesso:
            raise PortalChangedError('o portal devolveu uma nota diferente da solicitada')
        return nota

    def impressao_html(self, chave_acesso):
        """HTML da versão para impressão (não é o DANFSe oficial nem o XML)."""
        nota = self.get(chave_acesso)
        if not nota.row_key:
            raise PortalChangedError('link da versão para impressão não encontrado')
        response = self._session.get(f'{APP_PATH}/Notas/Visualizar/Impressao', params={'chave': nota.row_key})
        return response.text

    def download_xml(self, chave_acesso):
        raise CaptchaRequired(
            'o download do XML no portal web exige hCaptcha; use a API oficial (certificado digital) '
            'ou baixe manualmente em NFS-e Emitidas'
        )

    def download_danfse(self, chave_acesso):
        raise CaptchaRequired(
            'o download do DANFSe no portal web exige hCaptcha; use impressao_html() ou a API oficial'
        )

    def procurar_emitidas(self, *, documento, valor=None, nome=None, inicio=None, fim=None):
        """Notas não canceladas para o mesmo tomador (e valor), para detectar duplicidade."""
        digits = only_digits(documento)
        fim = fim or date.today()
        inicio = inicio or fim - timedelta(days=MAX_LIST_WINDOW_DAYS)
        encontradas = []
        for nota in self.iter_emitidas(inicio, fim, busca=nome or None):
            if nota.tomador_documento != digits or nota.situacao_codigo != SITUACAO_GERADA:
                continue
            if valor is not None and nota.valor != Decimal(valor).quantize(Decimal('0.01')):
                continue
            encontradas.append(nota)
        return encontradas


class Rascunhos:
    def __init__(self, session):
        self._session = session

    def list(self):
        response = self._session.get(f'{APP_PATH}/Notas/Rascunhos')
        return parse_rascunhos(response.text)

    def delete(self, draft_id, *, confirm=False):
        if not confirm:
            raise ConfirmationRequired('excluir rascunho exige confirm=True')
        self._session.post(f'{APP_PATH}/Rascunhos/Excluir/', data={'id': draft_id}, xhr=True)
        return all(r.id != draft_id for r in self.list())


class Emissao:
    """Emissão completa: Pessoas -> Serviço -> Valores -> Emitir NFS-e."""

    def __init__(self, session, consultas, notas, rascunhos, *, debug_dir=None):
        self._session = session
        self._consultas = consultas
        self._notas = notas
        self._rascunhos = rascunhos
        self._debug_dir = Path(debug_dir) if debug_dir else None

    # ------------------------------------------------------------- utilidades
    @staticmethod
    def _check_contract(html, fields, known_unsent, passo):
        live = set(form_field_names(html))
        sent = set(fields)
        missing = sorted(sent - live)
        unknown = sorted(live - sent - known_unsent)
        if missing or unknown:
            raise PortalChangedError(
                f'formulário "{passo}" mudou no portal; campos ausentes: {missing}; campos novos: {unknown}. '
                'Remapeie o passo antes de emitir.'
            )

    @staticmethod
    def _build(fields, values):
        """Monta o corpo do POST na ordem do navegador; campos sem valor vão vazios."""
        unknown = set(values) - set(fields)
        if unknown:
            raise PortalChangedError(f'campos fora do contrato: {sorted(unknown)}')
        return [(name, values.get(name, '')) for name in fields]

    @staticmethod
    def _expect_hidden(html, expected, passo):
        """Confere valores ocultos que caracterizam o cenário mapeado."""
        soup = soup_of(html)
        for name, value in expected.items():
            element = soup.find('input', attrs={'name': name, 'type': 'hidden'})
            if element is None:
                continue
            live = (element.get('value') or '').strip()
            if live.lower() != value.lower():
                raise UnsupportedScenario(
                    f'passo "{passo}": o portal indicou {name}={live!r} (mapeado: {value!r}); cenário não suportado'
                )

    def _post_step(self, path, params, body, passo, expected_next):
        response = self._session.post(path, params=params, data=body, follow=False)
        location = response.headers.get('location', '')
        if response.status_code in (301, 302, 303) and expected_next.lower() in location.lower():
            match = re.search(r'[?&]idr=([^&]+)', location)
            if not match:
                raise PortalChangedError(f'passo "{passo}" não devolveu o identificador do rascunho')
            return match.group(1)
        errors = parse_validation_errors(response.text) if response.status_code == 200 else []
        raise NfseValidationError(
            f'o portal recusou o passo "{passo}" (HTTP {response.status_code}): {"; ".join(errors) or location}',
            errors,
        )

    # ------------------------------------------------------------------ passos
    def _step_pessoas(self, request):
        competencia = request.competencia
        inscricao = self._session.inscricao
        html = self._session.get(f'{APP_PATH}/DPS/Pessoas').text
        self._check_contract(html, contracts.PESSOAS_FIELDS, contracts.PESSOAS_KNOWN_UNSENT, 'Pessoas')

        municipios = self._consultas.municipios_para_emissao(inscricao, competencia)
        if len(municipios) != 1:
            raise UnsupportedScenario(
                f'o portal devolveu {len(municipios)} municípios de emissão para este CNPJ; só o caso de um '
                'município foi mapeado'
            )
        municipio = municipios[0]
        opcao_sn = self._consultas.opcao_simples(inscricao, competencia)
        if opcao_sn.get('Tipo') != 3:
            raise UnsupportedScenario(
                f'opção no Simples Nacional não mapeada: {opcao_sn.get("Descricao")!r}. Este cliente cobre, por '
                'enquanto, empresas optantes do Simples Nacional como ME/EPP; veja os cenários suportados na '
                'documentação.'
            )
        estabelecimento = self._consultas.estabelecimento(inscricao, municipio['Codigo'], competencia)
        cadastro = estabelecimento.get('InfCad') or {}
        endereco = cadastro.get('InfoEndereco') or {}
        if only_digits(cadastro.get('Inscricao')) != inscricao:
            raise PortalChangedError('cadastro do estabelecimento não corresponde ao CNPJ logado')

        documento = validate_document(request.tomador.documento)
        consulta = self._consultas.tomador(documento, competencia)
        nome = consulta['nome'] or request.tomador.nome.strip()
        if not nome:
            raise NfseValidationError('nome do tomador não encontrado na RFB nem informado', ['nome ausente'])
        endereco_tomador = request.tomador.endereco
        if endereco_tomador is None and request.tomador.usar_endereco_rfb:
            endereco_tomador = consulta['endereco']

        values = {
            'NotaDoMei': 'False',
            'Prestador.LocalDomicilio': '0',
            'PreencherInfoIBSCBS': '0',
            'DataCompetencia': _br_date(competencia),
            'TipoEmitente': '1',
            'Prestador.EnderecoNacional.NomeMunicipio': municipio['Nome'],
            'Prestador.EnderecoNacional.CodigoMunicipio': str(municipio['Codigo']),
            'Prestador.InscricaoMunicipal': cadastro.get('InscricaoMunicipal') or '',
            'Prestador.Inscricao': format_document(inscricao),
            'Prestador.Nome': cadastro.get('RazaoSocial') or '',
            'SimplesNacional.Opcao': str(opcao_sn['Tipo']),
            'SimplesNacional.RegimeApuracaoTributosSN': str(request.tributacao.regime_apuracao_sn),
            'Prestador.Telefone': cadastro.get('Telefone') or '',
            'Prestador.Email': cadastro.get('Email') or '',
            'Prestador.EnderecoNacional.IndicadorEnderecoAlterado': 'False',
            'Prestador.EnderecoNacional.CEP': format_cep(endereco.get('CEP')),
            'Prestador.EnderecoNacional.Logradouro': endereco.get('Logradouro') or '',
            'Prestador.EnderecoNacional.Numero': endereco.get('Numero') or '',
            'Prestador.EnderecoNacional.Complemento': endereco.get('Complemento') or '',
            'Prestador.EnderecoNacional.Bairro': endereco.get('Bairro') or '',
            'Tomador.LocalDomicilio': '1',
            'Tomador.Inscricao': format_document(documento),
            'Tomador.Nome': nome,
            'Tomador.Telefone': request.tomador.telefone,
            'Tomador.Email': request.tomador.email,
            'Intermediario.LocalDomicilio': '0',
        }
        fields = list(contracts.PESSOAS_FIELDS)
        if endereco_tomador is not None:
            values.update({
                'Tomador.InformarEndereco': 'true',
                'Tomador.EnderecoNacional.CEP': format_cep(endereco_tomador.cep),
                'Tomador.EnderecoNacional.CodigoMunicipio': endereco_tomador.codigo_municipio,
                'Tomador.EnderecoNacional.NomeMunicipio': endereco_tomador.nome_municipio,
                'Tomador.EnderecoNacional.Bairro': endereco_tomador.bairro,
                'Tomador.EnderecoNacional.Logradouro': endereco_tomador.logradouro,
                'Tomador.EnderecoNacional.Numero': endereco_tomador.numero,
                'Tomador.EnderecoNacional.Complemento': endereco_tomador.complemento,
            })
        else:
            # Sem endereço, o navegador não envia o campo do checkbox.
            fields.remove('Tomador.InformarEndereco')
        draft_id = self._post_step(
            f'{APP_PATH}/DPS/Pessoas', None, self._build(fields, values), 'Pessoas', '/DPS/Servico'
        )
        return draft_id, nome, municipio

    def _step_servico(self, draft_id, request, municipio_emissor):
        servico = request.servico
        params = {'idr': draft_id}
        html = self._session.get(f'{APP_PATH}/DPS/Servico', params=params).text
        self._check_contract(html, contracts.SERVICO_FIELDS, contracts.SERVICO_KNOWN_UNSENT, 'Serviço')
        self._expect_hidden(html, {
            'ExigeGrupoCOMEX': 'NAO',
            'ServicoPrestado.PreencherInfoIBSCBS': 'NAO',
            'Obra.EhObrigatorio': 'False',
            'Evento.EhObrigatorio': 'False',
        }, 'Serviço')

        codigo_emissor = str(municipio_emissor['Codigo'])
        codigo_prestacao = servico.codigo_municipio_prestacao or codigo_emissor
        if codigo_prestacao == codigo_emissor:
            nome_prestacao = municipio_emissor['Nome']
        else:
            if not servico.nome_municipio_prestacao:
                raise NfseValidationError('informe nome_municipio_prestacao', ['município de prestação'])
            match = [
                m for m in self._consultas.buscar_municipio(servico.nome_municipio_prestacao.split('/')[0])
                if str(m.get('id')) == codigo_prestacao
            ]
            if not match:
                raise NfseValidationError('município de prestação não encontrado no portal', ['município'])
            nome_prestacao = match[0]['text']

        codigo_servico = servico.codigo_tributacao_nacional
        servicos = [s for s in self._consultas.buscar_servico(codigo_servico) if s.get('id') == codigo_servico]
        if not servicos:
            raise NfseValidationError(f'código de tributação nacional {codigo_servico} não encontrado', ['serviço'])
        if servico.codigo_nbs not in select_options(html, 'ServicoPrestado_CodigoNBS'):
            raise NfseValidationError(f'código NBS {servico.codigo_nbs} não existe no portal', ['NBS'])
        descricao = servico.descricao.strip()
        if not descricao or len(descricao) > 2000:
            raise NfseValidationError('descrição do serviço deve ter de 1 a 2000 caracteres', ['descrição'])

        incidencia = self._consultas.verificar_incidencia(
            municipio_emissor=hidden_value(html, 'hdfCodMunEmi') or codigo_emissor,
            municipio_tomador=hidden_value(html, 'hdfCodMunToma') or '',
            municipio_prestacao=codigo_prestacao,
            codigo_tributacao_nacional=codigo_servico,
            competencia=request.competencia,
            optante_mei=(hidden_value(html, 'hdfEhEmpresaMei') or '').lower() == 'true',
        )
        unsupported = [
            key for key in ('EhAtividadeObra', 'EhAtividadeEvento', 'EhExportacao') if incidencia.get(key)
        ]
        if (
            not incidencia.get('HaIncidenciaDeISSQN')
            or not incidencia.get('Conveniado')
            or not incidencia.get('EhConvenioVigente')
            or incidencia.get('ListaTributacaoMunicipal')
            or unsupported
        ):
            raise UnsupportedScenario(
                f'incidência do ISSQN fora do cenário mapeado: {incidencia.get("Mensagem") or incidencia}'
            )

        values = {
            'ExigeGrupoCOMEX': 'NAO',
            'LocalPrestacao.DescricaoMunicipioPrestacao': nome_prestacao,
            'ServicoPrestado.DescricaoCodigoTributacaoNacional': servicos[0]['text'],
            'ServicoPrestado.PreencherInfoIBSCBS': 'NAO',
            'LocalPrestacao.CodigoPaisPrestacao': 'BR',
            'LocalPrestacao.CodigoMunicipioPrestacao': codigo_prestacao,
            'ServicoPrestado.CodigoTributacaoNacional': codigo_servico,
            'ServicoPrestado.CodigoComplementarMunicipalObrigatorio': 'false',
            'ServicoPrestado.HaExportacaoImunidadeNaoIncidencia': '0',
            'ServicoPrestado.CodigoPaisResultadoEhObrigatorio': 'False',
            'MunicipioIncidencia.Codigo': str(incidencia['Codigo']),
            'MunicipioIncidencia.Tipo': str(incidencia['TipoMunicipioIncidencia']),
            'MunicipioIncidencia.EhConveniado': 'true',
            'MunicipioIncidencia.EhConvenioVigente': 'true',
            'MunicipioIncidencia.Nome': incidencia['Nome'],
            'ServicoPrestado.CodigoNBS': servico.codigo_nbs,
            'ServicoPrestado.Descricao': descricao,
            'Obra.EhObrigatorio': 'False',
            'Obra.EhObrigatorioObra': 'False',
            'Obra.EhObrigatorioImovel': 'False',
            'Evento.EhObrigatorio': 'False',
            'Complemento.InformacoesComplementares': servico.informacoes_complementares,
        }
        self._post_step(
            f'{APP_PATH}/DPS/Servico', params, self._build(contracts.SERVICO_FIELDS, values), 'Serviço',
            '/DPS/Tributacao',
        )

    def _step_tributacao(self, draft_id, request):
        tributacao = request.tributacao
        params = {'idr': draft_id}
        html = self._session.get(f'{APP_PATH}/DPS/Tributacao', params=params).text
        self._check_contract(html, contracts.TRIBUTACAO_FIELDS, contracts.TRIBUTACAO_KNOWN_UNSENT, 'Valores')
        self._expect_hidden(html, {
            'ISSQN.RegimeEspecialParametrizado': 'NENHUM',
            'ISSQN.AliquotaDeveSerInformada': 'False',
            'ValorTributos.PreencherInfoIBSCBS': 'NAO',
            'ISSQN.HaSuspensao': 'NAO',
            'ISSQN.HaBeneficioMunicipal': 'NAO',
            'TributacaoFederal.PISCofins.EhObrigatorio': 'False',
        }, 'Valores')
        values = {
            'ISSQN.RegimeEspecialParametrizado': 'NENHUM',
            'ISSQN.AliquotaDeveSerInformada': 'False',
            'ValorTributos.PreencherInfoIBSCBS': 'NAO',
            'Valores.ValorServico': format_money(request.valor),
            'ISSQN.TributacaoISSQN': '1',
            'ISSQN.RegimeEspecial': '0',
            'ISSQN.HaSuspensao': 'NAO',
            'ISSQN.HaRetencao': '0',
            'ISSQN.HaBeneficioMunicipal': 'NAO',
            'ISSQN.ValorMonetarioReducaoBCObrigatorio': 'False',
            'ISSQN.ValorPercentualReducaoBCObrigatorio': 'False',
            'ISSQN.HaDeducaoReducao': 'NAO',
            'DeducaoReducao.TipoDeducaoReducaoObrigatorio': 'NAO',
            'TributacaoFederal.PISCofins.EhObrigatorio': 'False',
            'ValorTributos.TipoValorTributos': str(tributacao.tipo_valor_tributos),
        }
        if tributacao.tipo_valor_tributos == 4:
            values['ValorTributos.AliquotaSN'] = format_money(tributacao.aliquota_sn)
        self._post_step(
            f'{APP_PATH}/DPS/Tributacao', params, self._build(contracts.TRIBUTACAO_FIELDS, values), 'Valores',
            '/DPS/EmitirNFSe',
        )

    def _conferir(self, draft_id, request):
        html = self._session.get(f'{APP_PATH}/DPS/EmitirNFSe', params={'idr': draft_id}).text
        conferencia = parse_conferencia(html)
        secoes = conferencia['secoes']
        problemas = []
        if conferencia['modo_emissao'] != 'DPS':
            raise UnsupportedScenario(
                f'modo de emissão {conferencia["modo_emissao"]!r} não mapeado (esperado emissão sem assinador)'
            )
        if conferencia['representado']:
            raise UnsupportedScenario('sessão representando outro contribuinte: cenário não mapeado')
        if conferencia['draft_id'] != draft_id:
            problemas.append('rascunho diferente do preparado')
        emitente = secoes.get('Informações do Emitente', {})
        if only_digits(emitente.get('CNPJ') or emitente.get('CPF')) != self._session.inscricao:
            problemas.append('emitente diferente do CNPJ logado')
        tomador = secoes.get('Tomador/Adquirente do Serviço', {})
        if only_digits(tomador.get('CPF') or tomador.get('CNPJ')) != only_digits(request.tomador.documento):
            problemas.append('tomador diferente do solicitado')
        valor = parse_money(secoes.get('Valores do Serviço Prestado', {}).get('Valor da operação/serviço prestado'))
        if valor != Decimal(request.valor).quantize(Decimal('0.01')):
            problemas.append(f'valor do serviço diferente do solicitado ({valor})')
        if secoes.get('Informações Gerais', {}).get('Data de Competência') != _br_date(request.competencia):
            problemas.append('competência diferente da solicitada')
        if problemas:
            raise NfseValidationError(
                f'conferência do rascunho {draft_id} falhou: {"; ".join(problemas)}', problemas
            )
        return secoes

    @staticmethod
    def _validate_request(request):
        tributacao = request.tributacao
        if tributacao.reter_issqn or tributacao.preencher_ibs_cbs:
            raise UnsupportedScenario('retenção de ISSQN e informações IBS/CBS ainda não foram mapeadas')
        if tributacao.regime_apuracao_sn != 1:
            raise UnsupportedScenario('apenas o regime de apuração 1 (tributos pelo Simples Nacional) foi mapeado')
        if tributacao.tipo_valor_tributos not in (3, 4):
            raise UnsupportedScenario('valor aproximado dos tributos: apenas as opções 3 e 4 foram mapeadas')
        if tributacao.tipo_valor_tributos == 4:
            if tributacao.aliquota_sn is None:
                raise NfseValidationError(
                    'informe a alíquota do Simples Nacional ou use tipo_valor_tributos=3 (não informar)', ['alíquota']
                )
            if not Decimal('0') < Decimal(tributacao.aliquota_sn) < Decimal('100'):
                raise NfseValidationError('alíquota do Simples Nacional inválida', ['alíquota'])
        servico = request.servico
        if not re.fullmatch(r'\d{2}\.\d{2}\.\d{2}', servico.codigo_tributacao_nacional or ''):
            raise NfseValidationError(
                'código de tributação nacional ausente ou fora do formato 00.00.00', ['serviço']
            )
        if not re.fullmatch(r'\d{9}', servico.codigo_nbs or ''):
            raise NfseValidationError('código NBS ausente ou fora do formato de 9 dígitos', ['NBS'])
        if not (servico.descricao or '').strip():
            raise NfseValidationError('descrição do serviço ausente', ['descrição'])
        if Decimal(request.valor) <= 0:
            raise NfseValidationError('o valor do serviço deve ser maior que zero', ['valor'])
        if request.competencia > date.today():
            raise NfseValidationError('a competência não pode ser futura', ['competência'])
        validate_document(request.tomador.documento)

    # ------------------------------------------------------------------ pública
    def prepare(self, request):
        """Cria o rascunho no portal, percorre os três passos e confere o resumo.

        Nada é emitido aqui: o resultado é um rascunho pronto, que pode ser emitido
        com `emit(..., confirm=True)` ou descartado com `discard()`.
        """
        self._validate_request(request)
        draft_id, tomador_nome, municipio = self._step_pessoas(request)
        try:
            self._step_servico(draft_id, request, municipio)
            self._step_tributacao(draft_id, request)
            resumo = self._conferir(draft_id, request)
        except Exception:
            self._discard_quietly(draft_id)
            raise
        return PreparedEmission(draft_id=draft_id, request=request, tomador_nome=tomador_nome, resumo=resumo)

    def _discard_quietly(self, draft_id):
        with contextlib.suppress(NfseHttpError, NfseAuthError):
            self._rascunhos.delete(draft_id, confirm=True)

    def discard(self, prepared):
        """Exclui o rascunho de uma emissão preparada e não emitida."""
        return self._rascunhos.delete(prepared.draft_id, confirm=True)

    def emit(self, prepared, *, confirm=False, permitir_duplicidade=False):
        """Emite a NFS-e de um rascunho preparado. Irreversível (só cancelamento posterior).

        A chamada nunca é repetida automaticamente. Se o resultado não puder ser
        confirmado, lança EmissionUncertainError: confira as notas emitidas antes de
        qualquer nova tentativa.
        """
        if not confirm:
            raise ConfirmationRequired('emitir NFS-e exige confirm=True')
        request = prepared.request
        if not permitir_duplicidade:
            existentes = self._notas.procurar_emitidas(
                documento=request.tomador.documento, valor=request.valor, nome=prepared.tomador_nome
            )
            if existentes:
                chaves = [nota.chave_acesso for nota in existentes]
                raise NfseValidationError(
                    'já existe NFS-e ativa para o mesmo tomador e valor nos últimos 30 dias; '
                    f'use permitir_duplicidade=True se for intencional: {chaves}',
                    chaves,
                )
        self._session.check_alive()
        self._conferir(prepared.draft_id, request)
        try:
            response = self._session.get(
                f'{APP_PATH}/DPS/NFSe/Index', params={'idr': prepared.draft_id}, retry_auth=False
            )
        except (NfseHttpError, NfseAuthError) as exc:
            raise EmissionUncertainError(
                f'falha ao enviar a emissão do rascunho {prepared.draft_id}: {exc}. '
                'Confira as notas emitidas antes de repetir.',
                draft_id=prepared.draft_id,
            ) from exc
        debug_path = self._save_debug(prepared.draft_id, response)
        chave = self._find_chave(response)
        if not chave:
            errors = parse_validation_errors(response.text)
            if errors:
                raise EmissionRejectedError(
                    f'o portal não emitiu o rascunho {prepared.draft_id}: {"; ".join(errors)}', errors
                )
            raise EmissionUncertainError(
                f'emissão enviada, mas a chave de acesso não foi localizada (rascunho {prepared.draft_id}). '
                'Confira as notas emitidas antes de repetir.',
                draft_id=prepared.draft_id,
                final_url=response.url,
                debug_path=str(debug_path) if debug_path else None,
            )
        nota = self._notas.get(chave)
        if nota.tomador_documento != only_digits(request.tomador.documento) or nota.valor_servico != Decimal(
            request.valor
        ).quantize(Decimal('0.01')):
            raise EmissionUncertainError(
                f'a nota {chave} foi localizada, mas tomador ou valor não conferem com o solicitado',
                draft_id=prepared.draft_id,
                final_url=response.url,
                debug_path=str(debug_path) if debug_path else None,
            )
        return EmissionResult(chave_acesso=chave, draft_id=prepared.draft_id, final_url=response.url, nota=nota)

    @staticmethod
    def _find_chave(response):
        resultado = parse_emissao_resultado(response.text)
        if resultado['sucesso']:
            return resultado['chave_acesso']
        for text in [response.url, *response.redirects]:
            match = CHAVE_RE.search(text or '')
            if match:
                return match.group(1)
        soup = soup_of(response.text)
        for link in soup.select('a[href*="/Notas/Visualizar/Index/"], a[href*="/Notas/Download/"]'):
            match = CHAVE_RE.search(link.get('href', ''))
            if match:
                return match.group(1)
        chaves = set(CHAVE_RE.findall(soup.get_text(' ')))
        return chaves.pop() if len(chaves) == 1 else None

    def _save_debug(self, draft_id, response):
        if not self._debug_dir:
            return None
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        path = self._debug_dir / f'emissao-{draft_id}-{int(time.time())}.html'
        path.write_text(f'<!-- {response.url} | {response.redirects} -->\n{response.text}', encoding='utf-8')
        path.chmod(0o600)
        return path


class Cancelamento:
    """Cancelamento de NFS-e (evento 101101) pelo portal."""

    def __init__(self, session, notas, *, debug_dir=None):
        self._session = session
        self._notas = notas
        self._debug_dir = Path(debug_dir) if debug_dir else None

    def _save_debug(self, chave_acesso, text):
        if not self._debug_dir:
            return
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        path = self._debug_dir / f'cancelamento-{chave_acesso}-{int(time.time())}.json'
        path.write_text(text, encoding='utf-8')
        path.chmod(0o600)

    def preview(self, chave_acesso):
        """Abre o formulário de cancelamento sem enviar nada. Não altera a nota."""
        nota = self._notas.get(chave_acesso)
        if nota.cancelada:
            raise CancellationNotAllowed(f'a nota {chave_acesso} já está cancelada ({nota.situacao})')
        if not nota.row_key:
            raise PortalChangedError('identificador interno da nota não encontrado')
        response = self._session.post(
            f'{APP_PATH}/Notas/Emitidas/ModalCancelamento/', data={'chave': nota.row_key}, xhr=True
        )
        data = response.json()
        if not data.get('Sucesso'):
            raise CancellationNotAllowed(data.get('Mensagem') or 'o portal não permitiu abrir o cancelamento')
        preview = parse_cancel_modal(data.get('Html') or '', data.get('TipoEvento'), data.get('Mensagem'))
        if preview.chave_acesso != chave_acesso:
            raise PortalChangedError('o formulário de cancelamento é de outra nota')
        return preview

    def cancel(self, chave_acesso, *, motivo, justificativa, confirm=False, permitir_analise_fiscal=False):
        """Cancela a NFS-e. Motivos: '1' erro na emissão, '2' serviço não prestado, '9' outros.

        Fora do prazo de cancelamento direto, o portal só aceita um pedido de análise
        fiscal ao município (evento 101103); isso exige `permitir_analise_fiscal=True`.
        """
        justificativa = (justificativa or '').strip()
        if not 15 <= len(justificativa) <= 255:
            raise NfseValidationError('a justificativa deve ter de 15 a 255 caracteres', ['justificativa'])
        if not confirm:
            raise ConfirmationRequired('cancelar NFS-e exige confirm=True')
        preview = self.preview(chave_acesso)
        if str(motivo) not in preview.motivos:
            raise NfseValidationError(f'motivo inválido; opções: {preview.motivos}', ['motivo'])
        if not preview.cancelamento_direto and not permitir_analise_fiscal:
            raise CancellationNotAllowed(
                f'cancelamento direto indisponível: {preview.mensagem or "exige análise fiscal do município"}',
                tipo_evento=preview.tipo_evento,
            )
        body = dict(preview.fields)
        body['MotivoCancelamento'] = str(motivo)
        body['Justificativa'] = justificativa
        response = self._session.post(preview.form_action, data=body, xhr=True, retry_auth=False)
        self._save_debug(chave_acesso, response.text)
        try:
            result = response.json()
        except ValueError as exc:
            raise NfseHttpError(
                'o portal respondeu ao cancelamento em formato inesperado; confira a situação da nota '
                'antes de repetir',
                status_code=response.status_code, method='POST', url=response.url,
            ) from exc
        if result.get('AssinarPedRegEvento'):
            raise UnsupportedScenario('o portal pediu assinatura com certificado digital para este evento')
        if not result.get('Processado'):
            errors = parse_validation_errors(result.get('HTML') or '')
            raise NfseValidationError(f'cancelamento não processado: {"; ".join(errors)}', errors)
        sucesso = bool(result.get('Sucesso'))
        tipo_evento = result.get('TipoEvento') or preview.tipo_evento or EVENTO_CANCELAMENTO
        nota = self._notas.get(chave_acesso)
        if sucesso and tipo_evento == EVENTO_CANCELAMENTO and not nota.cancelada:
            raise NfseHttpError(
                f'o portal confirmou o cancelamento da nota {chave_acesso}, mas a nota ainda aparece ativa; '
                'confira no portal antes de qualquer nova ação',
                status_code=response.status_code, method='POST', url=response.url,
            )
        return CancelResult(
            chave_acesso=chave_acesso,
            tipo_evento=tipo_evento,
            sucesso=sucesso,
            mensagem=result.get('Mensagem') or '',
            situacao_apos=nota.situacao,
        )


class EmissorNacionalClient:
    """Ponto de entrada: `client.notas`, `client.emissao`, `client.cancelamento`, `client.rascunhos`."""

    def __init__(self, session, *, debug_dir=None):
        self.session = session
        self.empresa = None
        self.consultas = Consultas(session)
        self.notas = Notas(session)
        self.rascunhos = Rascunhos(session)
        self.emissao = Emissao(session, self.consultas, self.notas, self.rascunhos, debug_dir=debug_dir)
        self.cancelamento = Cancelamento(session, self.notas, debug_dir=debug_dir)

    @classmethod
    def from_profile(cls, empresa=None, *, home=None, env_file=None, session_file=None, debug_dir=None,
                     **session_kwargs):
        """Cliente de uma empresa conectada com `nfseai conectar` (ou pelas variáveis de ambiente).

        A sessão do portal fica em ``~/.nfseai/sessions/<empresa>.json`` e as respostas das
        emissões em ``~/.nfseai/debug``, salvo quando outro caminho é informado.
        """
        from .profiles import Store

        store = Store(home=home, env_file=env_file)
        name = store.resolve(empresa)
        credentials = store.load_credentials(name)
        store.ensure_home()
        if session_file is None:
            store.private_dir(store.session_file(name).parent)
            session_file = store.session_file(name)
        if debug_dir is None:
            debug_dir = store.private_dir(store.debug_dir)
        session = PortalSession(credentials, session_file=session_file, **session_kwargs)
        client = cls(session, debug_dir=debug_dir)
        client.empresa = name
        return client

    @classmethod
    def from_credentials(cls, user, password, *, session_file=None, debug_dir=None, **session_kwargs):
        """Cliente a partir de CNPJ/CPF e senha já em memória (nada é gravado em disco)."""
        session = PortalSession(Credentials(user=user, password=password), session_file=session_file,
                                **session_kwargs)
        return cls(session, debug_dir=debug_dir)

    def whoami(self):
        """Identificação do contribuinte logado, lida da página inicial."""
        response = self.session.get(APP_PATH)
        return parse_identity(response.text)

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
