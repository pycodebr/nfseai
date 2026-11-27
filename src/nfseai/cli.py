"""Linha de comando `nfseai`: conexão, consulta, emissão, cancelamento, PDF e registro.

A saída padrão é JSON, para um agente de IA ou um script ler sem ambiguidade. As
exceções são os assistentes interativos (`conectar` e `configurar` sem opções), que
conversam com a pessoa no terminal.

Códigos de saída: 0 sucesso; 1 erro; 2 uso incorreto; 3 empresa não conectada ao
portal; 4 padrões fiscais incompletos.

Cada empresa usa um caminho: o portal, com CNPJ e senha (caminho B), ou a API oficial da
Sefin Nacional, com certificado digital A1 (caminho A, `conectar --certificado`).
"""
import argparse
import json
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import __version__, operations
from .client import EmissorNacionalClient
from .config import APP_PATH, BASE_URL, format_document, only_digits
from .errors import (
    ConfirmationRequired,
    FiscalProfileIncomplete,
    NfseError,
    NfseValidationError,
    NotConnectedError,
    ProfileError,
)
from .ledger import Ledger
from .models import EmissionRequest, Endereco, Tomador
from .pdf import default_pdf_name, pdf_support_available, render_nota_pdf
from .profiles import TRANSPORTE_SEFIN, Store, normalize_service_code, validate_fiscal
from .wizard import Console, WizardAborted, configure, connect, connect_certificate

MOTIVOS = {'erro': '1', 'nao-prestado': '2', 'outros': '9'}
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NOT_CONNECTED, EXIT_FISCAL_INCOMPLETE = 0, 1, 2, 3, 4
SO_PORTAL = frozenset({
    'emitidas', 'recebidas', 'rascunhos', 'rascunho-excluir', 'whoami', 'servicos', 'municipios', 'pdf', 'impressao',
})
SO_SEFIN = frozenset({'xml', 'dps'})
TOMADOR_ENDERECO = ('tomador_cep', 'tomador_municipio', 'tomador_logradouro', 'tomador_numero', 'tomador_bairro')
CONNECT_HINT = (
    'peça ao usuário para rodar `nfseai conectar` no terminal dele (a senha é digitada lá, nunca no chat) '
    'e avise quando terminar'
)


# ------------------------------------------------------------------- utilidades
def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f'tipo não serializável: {type(value)!r}')


def _print(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=_json_default))


def parse_date(text):
    """Aceita DD/MM/AAAA e AAAA-MM-DD."""
    for pattern in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text.strip(), pattern).date()
        except ValueError:
            continue
    raise NfseValidationError(f'data inválida: {text!r} (use DD/MM/AAAA ou AAAA-MM-DD)', ['data'])


def parse_valor(text):
    """Aceita '1047.51', '1047,51' e '1.047,51'. Recusa valores ambíguos como '1.047'."""
    raw = (text or '').replace('R$', '').strip()
    normalized = raw.replace('.', '').replace(',', '.') if ',' in raw else raw
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        raise NfseValidationError(f'valor inválido: {text!r} (ex.: 1047.51 ou 1.047,51)', ['valor']) from None
    if value.as_tuple().exponent < -2:
        raise NfseValidationError(
            f'valor ambíguo: {text!r}. Escreva com centavos (ex.: 1047.00 ou 1.047,00).', ['valor']
        )
    if value <= 0:
        raise NfseValidationError('o valor do serviço deve ser maior que zero', ['valor'])
    return value.quantize(Decimal('0.01'))


def _parse_rate(text):
    try:
        return Decimal(text.replace('%', '').replace(',', '.').strip()).quantize(Decimal('0.01'))
    except InvalidOperation:
        raise NfseValidationError(f'alíquota inválida: {text!r} (ex.: 6.00)', ['alíquota']) from None


def _require_tty(command):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise UsageError(
            f'`nfseai {command}` é um assistente interativo e precisa ser rodado pelo próprio usuário, em um '
            'terminal. Um agente de IA não deve rodá-lo nem pedir a senha no chat: '
            + CONNECT_HINT + '.'
        )


class UsageError(NfseError):
    """Comando chamado de uma forma que não pode ser atendida."""


# ----------------------------------------------------------------------- parser
def _add_emission_args(parser):
    parser.add_argument('--documento', required=True, help='CPF ou CNPJ do tomador (cliente)')
    parser.add_argument('--valor', required=True, help='valor do serviço, ex.: 1047.51 ou 1.047,51')
    parser.add_argument('--competencia', help='data de competência, DD/MM/AAAA ou AAAA-MM-DD (padrão: hoje)')
    parser.add_argument('--referencia', help='identificador da venda/contrato no seu sistema; evita nota em dobro')
    parser.add_argument('--nome', help='nome do tomador, usado só se a Receita Federal não devolver o nome')
    parser.add_argument('--email', help='e-mail do tomador (opcional)')
    parser.add_argument('--telefone', help='telefone do tomador (opcional)')
    parser.add_argument('--descricao', help='descrição do serviço (padrão: a da empresa)')
    parser.add_argument('--codigo-servico', dest='codigo_servico', help='código de tributação nacional, ex.: 08.02.01')
    parser.add_argument('--nbs', help='código NBS, 9 dígitos')
    parser.add_argument('--aliquota-sn', dest='aliquota_sn', help='alíquota do Simples Nacional em %%, ex.: 6.00')
    parser.add_argument('--sem-valor-tributos', dest='sem_valor_tributos', action='store_true',
                        help='não informar o valor aproximado dos tributos')
    parser.add_argument('--info-complementar', dest='info_complementar', help='informações complementares da nota')
    parser.add_argument('--municipio-prestacao', dest='municipio_prestacao',
                        help='código IBGE do município de prestação, se diferente do município da empresa')
    parser.add_argument('--nome-municipio-prestacao', dest='nome_municipio_prestacao',
                        help='nome do município de prestação, como o portal mostra')
    _add_sefin_fiscal_args(parser)
    parser.add_argument('--reter-iss', dest='reter_iss', action='store_true',
                        help='caminho A: ISSQN retido pelo tomador (exige o endereço do tomador)')
    parser.add_argument('--sem-irrf', dest='sem_irrf', action='store_true',
                        help='caminho A: emite esta nota sem a retenção de IRRF gravada nos padrões')
    for campo, ajuda in (
        ('cep', 'CEP'), ('municipio', 'código IBGE do município'), ('logradouro', 'logradouro'),
        ('numero', 'número'), ('bairro', 'bairro'), ('complemento', 'complemento (opcional)'),
    ):
        parser.add_argument(f'--tomador-{campo}', dest=f'tomador_{campo}', help=f'caminho A: {ajuda} do tomador')


def _add_sefin_fiscal_args(parser):
    parser.add_argument('--aliquota-iss', dest='aliquota_iss',
                        help='caminho A: alíquota do ISSQN em %%, só quando o município de incidência exigir')
    parser.add_argument('--aliquota-irrf', dest='aliquota_irrf',
                        help='caminho A: alíquota do IRRF retido pelo tomador, em %% (ex.: 1,5)')
    for esfera in ('federais', 'estaduais', 'municipais'):
        parser.add_argument(f'--tributos-{esfera}', dest=f'tributos_{esfera}',
                            help=f'caminho A: percentual aproximado dos tributos {esfera} (Lei 12.741/2012)')


def _add_period_args(parser):
    parser.add_argument('--inicio', help='DD/MM/AAAA ou AAAA-MM-DD')
    parser.add_argument('--fim', help='DD/MM/AAAA ou AAAA-MM-DD')
    parser.add_argument('--busca', help='nome do tomador')
    parser.add_argument('--pagina', type=int, default=1)
    parser.add_argument('--todas', action='store_true', help='percorre todas as páginas do período')


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--empresa', default=argparse.SUPPRESS, help='apelido da empresa (padrão: a empresa padrão)')
    common.add_argument('--env-file', dest='env_file', default=argparse.SUPPRESS,
                        help='arquivo dotenv com NFSEAI_USER/NFSEAI_PASSWORD')

    parser = argparse.ArgumentParser(
        prog='nfseai',
        description='NFS-e pelo Emissor Nacional (www.nfse.gov.br): consulta, emissão, cancelamento e PDF.',
        epilog='Primeiro uso: `nfseai conectar` (no seu terminal), depois `nfseai status`.',
    )
    parser.add_argument('--version', action='version', version=f'nfseai {__version__}')
    parser.add_argument('--empresa', help='apelido da empresa (padrão: a empresa padrão)')
    parser.add_argument('--env-file', dest='env_file', help='arquivo dotenv com NFSEAI_USER/NFSEAI_PASSWORD')
    sub = parser.add_subparsers(dest='command', required=True, metavar='comando')

    def add(name, help_text, aliases=()):
        return sub.add_parser(name, help=help_text, aliases=list(aliases), parents=[common])

    conectar = add('conectar', 'assistente: informa CNPJ e senha do portal e testa o login', aliases=['connect'])
    conectar.add_argument('--sem-teste', dest='sem_teste', action='store_true', help='grava sem testar o login')
    conectar.add_argument('--padrao', action='store_true', help='torna esta a empresa padrão')
    conectar.add_argument('--sem-configurar', dest='sem_configurar', action='store_true',
                          help='não pergunta os padrões fiscais depois de conectar')
    conectar.add_argument('--certificado', metavar='ARQUIVO',
                          help='caminho A: conecta pelo certificado digital A1 (.pfx), pela API da Sefin Nacional')
    conectar.add_argument('--ambiente', choices=['producao-restrita', 'producao'], default='producao-restrita',
                          help='caminho A: producao-restrita (homologação, padrão) ou producao')
    conectar.add_argument('--municipio', help='caminho A: código IBGE do município emissor da empresa')
    conectar.add_argument('--inscricao-municipal', dest='inscricao_municipal', help='caminho A: inscrição municipal')
    conectar.add_argument('--serie', type=int, default=1, help='caminho A: série da DPS (1 a 49999, padrão 1)')

    desconectar = add('desconectar', 'apaga a senha e a sessão gravadas da empresa', aliases=['disconnect'])
    desconectar.add_argument('--confirm', action='store_true')

    status = add('status', 'mostra se a empresa está conectada e configurada')
    status.add_argument('--online', action='store_true', help='testa o login no portal')

    add('empresas', 'lista as empresas configuradas nesta máquina')
    usar = add('usar', 'define a empresa padrão')
    usar.add_argument('nome')

    config = add('configurar', 'grava os padrões fiscais das notas (assistente se nenhuma opção for passada)')
    config.add_argument('--codigo-servico', dest='codigo_servico', help='código de tributação nacional, ex.: 08.02.01')
    config.add_argument('--nbs', help='código NBS, 9 dígitos')
    config.add_argument('--descricao', help='descrição padrão do serviço')
    config.add_argument('--aliquota-sn', dest='aliquota_sn', help='alíquota do Simples Nacional em %%')
    config.add_argument('--sem-valor-tributos', dest='sem_valor_tributos', action='store_true')
    config.add_argument('--info-complementar', dest='info_complementar')
    config.add_argument('--municipio-prestacao', dest='municipio_prestacao', help='código IBGE; "" volta ao padrão')
    config.add_argument('--nome-municipio-prestacao', dest='nome_municipio_prestacao')
    _add_sefin_fiscal_args(config)
    config.add_argument('--serie', type=int,
                        help='caminho A: série da DPS (1 a 49999), uma que nenhum outro sistema da empresa use')
    config.add_argument('--leiaute', choices=['1.01', '1.01-nt009', 'padrao'],
                        help='caminho A: força o leiaute da DPS (virada da NT 009); "padrao" volta ao do ambiente')

    add('doctor', 'confere a instalação, as permissões e o acesso ao portal')
    add('instalar-pdf', 'instala o suporte a PDF (Playwright + Chromium)')
    add('instalar-certificado', 'instala o suporte a certificado digital do caminho A (cryptography)')
    add('whoami', 'contribuinte logado no portal')

    servicos = add('servicos', 'pesquisa códigos de tributação nacional')
    servicos.add_argument('termo')
    municipios = add('municipios', 'pesquisa municípios (código IBGE)')
    municipios.add_argument('termo')

    _add_period_args(add('emitidas', 'lista NFS-e emitidas'))
    _add_period_args(add('recebidas', 'lista NFS-e recebidas'))
    nota = add('nota', 'detalhe de uma NFS-e')
    nota.add_argument('chave', help='chave de acesso de 50 dígitos')

    add('rascunhos', 'lista rascunhos do portal')
    excluir = add('rascunho-excluir', 'exclui um rascunho do portal')
    excluir.add_argument('id')
    excluir.add_argument('--confirm', action='store_true')

    preparar = add('preparar', 'ensaio: monta o rascunho, mostra o resumo e descarta; nada é emitido')
    _add_emission_args(preparar)
    preparar.add_argument('--manter', action='store_true', help='mantém o rascunho no portal')

    emitir = add('emitir', 'emite a NFS-e; sem --confirm faz só o ensaio')
    _add_emission_args(emitir)
    emitir.add_argument('--confirm', action='store_true', help='confirma a emissão (ato fiscal, irreversível)')
    emitir.add_argument('--reemitir', action='store_true', help='nova nota para referência com nota cancelada')
    emitir.add_argument('--permitir-duplicidade', dest='permitir_duplicidade', action='store_true',
                        help='emite mesmo havendo nota ativa para o mesmo tomador e valor nos últimos 30 dias')
    emitir.add_argument('--pdf', nargs='?', const='', default=None, metavar='ARQUIVO',
                        help='gera o PDF da nota depois de emitir (caminho opcional)')

    previa = add('cancelar-previa', 'mostra se a nota pode ser cancelada, sem cancelar')
    previa.add_argument('chave')
    cancelar = add('cancelar', 'cancela uma NFS-e; sem --confirm mostra só a prévia')
    cancelar.add_argument('chave')
    cancelar.add_argument('--motivo', required=True, choices=sorted(MOTIVOS))
    cancelar.add_argument('--justificativa', required=True, help='de 15 a 255 caracteres')
    cancelar.add_argument('--confirm', action='store_true', help='confirma o cancelamento (ato fiscal)')
    cancelar.add_argument('--permitir-analise-fiscal', dest='permitir_analise_fiscal', action='store_true')

    pdf = add('pdf', 'gera o PDF da versão para impressão de uma NFS-e')
    pdf.add_argument('chave')
    pdf.add_argument('--out', help='arquivo de saída (padrão: ~/.nfseai/pdf/NFSe_<numero>_<chave>.pdf)')
    impressao = add('impressao', 'salva o HTML da versão para impressão de uma NFS-e')
    impressao.add_argument('chave')
    impressao.add_argument('--out', required=True)

    xml = add('xml', 'caminho A: salva o XML oficial de uma NFS-e')
    xml.add_argument('chave')
    xml.add_argument('--out', required=True)
    dps = add('dps', 'caminho A: consulta na Sefin se uma DPS gerou NFS-e (para conferir emissão incerta)')
    dps.add_argument('id', help='identificador da DPS (DPS + 42 dígitos)')

    registro = add('registro', 'registro local das emissões (controle contra nota em dobro)')
    registro.add_argument('--referencia')
    registro.add_argument('--status', choices=['preparada', 'emitida', 'incerta', 'erro', 'cancelada'])
    registro.add_argument('--todas-empresas', dest='todas_empresas', action='store_true')
    registro.add_argument('--limite', type=int, default=50)
    registrar = add('registrar', 'associa a uma referência uma nota que já existe no portal')
    registrar.add_argument('--referencia', required=True)
    registrar.add_argument('--chave', required=True)
    descartar = add('registro-descartar', 'libera uma referência sem nota válida (preparada, incerta ou erro)')
    descartar.add_argument('--referencia', required=True)
    descartar.add_argument('--confirm', action='store_true')
    return parser


# --------------------------------------------------------------------- comandos
def _fiscal_from_args(store, empresa, args):
    aliquota = _parse_rate(args.aliquota_sn) if args.aliquota_sn else None
    tipo = 3 if args.sem_valor_tributos else (4 if aliquota is not None else None)
    fiscal = store.fiscal(empresa).merged(
        codigo_tributacao_nacional=normalize_service_code(args.codigo_servico) if args.codigo_servico else None,
        codigo_nbs=only_digits(args.nbs) if args.nbs else None,
        descricao=args.descricao,
        informacoes_complementares=args.info_complementar,
        codigo_municipio_prestacao=args.municipio_prestacao,
        nome_municipio_prestacao=args.nome_municipio_prestacao,
        tipo_valor_tributos=tipo,
        aliquota_sn=aliquota,
        aliquota_iss=_parse_rate(args.aliquota_iss) if getattr(args, 'aliquota_iss', None) else None,
        aliquota_irrf=_parse_rate(args.aliquota_irrf) if getattr(args, 'aliquota_irrf', None) else None,
        **{
            f'tributos_{esfera}': _parse_rate(getattr(args, f'tributos_{esfera}'))
            for esfera in ('federais', 'estaduais', 'municipais')
            if getattr(args, f'tributos_{esfera}', None) is not None
        },
    )
    validate_fiscal(fiscal)
    return fiscal


def _endereco_tomador(args):
    informados = [campo for campo in TOMADOR_ENDERECO if getattr(args, campo, None)]
    if not informados and not getattr(args, 'tomador_complemento', None):
        return None
    faltando = [campo for campo in TOMADOR_ENDERECO if campo not in informados]
    if faltando:
        opcoes = ', '.join('--' + campo.replace('_', '-') for campo in faltando)
        raise NfseValidationError(f'endereço do tomador incompleto: falta {opcoes}', ['endereço'])
    municipio = only_digits(args.tomador_municipio)
    if len(municipio) != 7:
        raise NfseValidationError('--tomador-municipio é o código IBGE de 7 dígitos', ['endereço'])
    return Endereco(
        cep=only_digits(args.tomador_cep), codigo_municipio=municipio, nome_municipio='', bairro=args.tomador_bairro,
        logradouro=args.tomador_logradouro, numero=args.tomador_numero, complemento=args.tomador_complemento or '',
    )


def _request_from_args(store, empresa, args):
    transporte = store.transporte(empresa)
    fiscal = _fiscal_from_args(store, empresa, args)
    fiscal.require_complete(transporte)
    endereco = _endereco_tomador(args)
    valor = parse_valor(args.valor)
    return EmissionRequest(
        competencia=parse_date(args.competencia) if args.competencia else date.today(),
        tomador=Tomador(
            documento=args.documento, nome=args.nome or '', email=args.email or '', telefone=args.telefone or '',
            endereco=endereco,
        ),
        valor=valor,
        servico=fiscal.servico(transporte),
        tributacao=fiscal.tributacao(
            transporte, reter_issqn=getattr(args, 'reter_iss', False), valor=valor,
            reter_irrf=not getattr(args, 'sem_irrf', False),
        ),
        referencia=(args.referencia or '').strip(),
    )


def _leiaute_do_ambiente(ambiente):
    from .dps import leiaute_padrao
    from .sefin import AMBIENTES

    return leiaute_padrao(AMBIENTES[ambiente][1])


def _status_sefin(store, empresa, args):
    from .errors import CertificateError

    origem = store.certificate_origin(empresa)
    config = store.sefin_config(empresa)
    fiscal = store.fiscal(empresa)
    faltando = fiscal.missing(TRANSPORTE_SEFIN)
    data = {
        'empresa': empresa,
        'caminho': TRANSPORTE_SEFIN,
        'conectada': bool(origem),
        'origem_credenciais': origem,
        'ambiente': config['ambiente'],
        'leiaute': config.get('leiaute') or _leiaute_do_ambiente(config['ambiente']),
        'emitente': {k: config.get(k) for k in ('cnpj', 'codigo_municipio', 'inscricao_municipal', 'serie')},
        'empresa_padrao': store.default_profile(),
        'empresas': store.profiles(),
        'perfil_fiscal': {'completo': not faltando, 'faltando': faltando, **fiscal.as_dict()},
        'pasta': str(store.home),
        'versao': __version__,
    }
    if not origem:
        data['proximo_passo'] = (
            'peça ao usuário para rodar `nfseai conectar --certificado <arquivo.pfx>` no terminal dele (a senha '
            'do certificado é digitada lá, nunca no chat) e avise quando terminar'
        )
        return data, EXIT_NOT_CONNECTED
    try:
        certificado = store.load_certificate(empresa)
        certificado.conferir(config['cnpj'])
        data['certificado'] = {
            'cnpj': certificado.cnpj, 'titular': certificado.titular,
            'valido_ate': certificado.valido_ate.date().isoformat(),
        }
    except (CertificateError, NotConnectedError) as exc:
        data['certificado'] = {'ok': False, 'erro': str(exc)}
        data['proximo_passo'] = 'o certificado precisa ser renovado ou conectado de novo pelo usuário'
        return data, EXIT_ERROR
    if args.online:
        data['online'] = 'o teste de conexão com a Sefin entra depois da validação na produção restrita'
    if faltando:
        data['proximo_passo'] = (
            'grave os padrões fiscais com `nfseai configurar --codigo-servico ... --nbs ... --descricao \"...\" '
            '--tributos-federais ... --tributos-estaduais ... --tributos-municipais ...` (os valores vêm do '
            'contador)'
        )
        return data, EXIT_FISCAL_INCOMPLETE
    data['proximo_passo'] = 'pronto; antes de emitir, faça o ensaio com `nfseai preparar` (nada é enviado à Sefin)'
    return data, EXIT_OK


def _status(store, args):
    known = store.profiles()
    try:
        empresa = store.resolve(getattr(args, 'empresa', None))
    except ProfileError as exc:
        return {'conectada': False, 'empresas': known, 'erro': str(exc)}, EXIT_ERROR
    if store.transporte(empresa) == TRANSPORTE_SEFIN:
        return _status_sefin(store, empresa, args)
    origem = store.credentials_origin(empresa)
    login = store.login_user(empresa)
    fiscal = store.fiscal(empresa)
    faltando = fiscal.missing()
    data = {
        'empresa': empresa,
        'caminho': 'portal',
        'conectada': bool(origem),
        'origem_credenciais': origem,
        'login': format_document(login) if login and len(login) in (11, 14) else login,
        'empresa_padrao': store.default_profile(),
        'empresas': known,
        'perfil_fiscal': {'completo': not faltando, 'faltando': faltando, **fiscal.as_dict()},
        'suporte_pdf': pdf_support_available(),
        'pasta': str(store.home),
        'versao': __version__,
    }
    code = EXIT_OK
    if not origem:
        data['proximo_passo'] = CONNECT_HINT
        return data, EXIT_NOT_CONNECTED
    if args.online:
        try:
            with _client(store, empresa) as client:
                data['portal'] = {'ok': True, **client.whoami()}
        except NfseError as exc:
            data['portal'] = {'ok': False, 'erro': f'{type(exc).__name__}: {exc}'}
            data['proximo_passo'] = 'confira a senha no portal pelo navegador e peça ao usuário para rodar ' \
                                    '`nfseai conectar` de novo'
            return data, EXIT_ERROR
    if faltando:
        data['proximo_passo'] = (
            'grave os padrões fiscais com `nfseai configurar --codigo-servico ... --nbs ... --descricao "..." '
            '--aliquota-sn ...` (pergunte os valores ao usuário; eles vêm do contador) ou informe-os em cada emissão'
        )
        code = EXIT_FISCAL_INCOMPLETE
    else:
        data['proximo_passo'] = 'pronto para consultar; antes de emitir, faça o ensaio com `nfseai preparar`'
    return data, code


def _empresa_view(store, name):
    transporte = store.transporte(name)
    origem = store.certificate_origin(name) if transporte == TRANSPORTE_SEFIN else store.credentials_origin(name)
    return {
        'nome': name,
        'caminho': transporte,
        'conectada': bool(origem),
        'origem_credenciais': origem,
        'perfil_fiscal_completo': not store.fiscal(name).missing(transporte),
    }


def _client(store, empresa):
    return EmissorNacionalClient.from_profile(empresa, home=store.home, env_file=store.env_file)


def _ledger(store):
    store.ensure_home()
    return Ledger(store.ledger_path)


def _doctor(store):
    checks = []

    def check(name, ok, detail, required=True):
        checks.append({'item': name, 'ok': bool(ok), 'obrigatorio': required, 'detalhe': detail})

    check('python', sys.version_info >= (3, 10), sys.version.split()[0])
    for module in ('httpx', 'bs4', 'lxml', 'dotenv'):
        try:
            __import__(module)
            check(f'dependência {module}', True, 'instalada')
        except ImportError as exc:
            check(f'dependência {module}', False, str(exc))
    home = store.home
    if home.exists():
        mode = home.stat().st_mode & 0o777
        check('pasta de dados', mode == 0o700, f'{home} (modo {mode:o}; esperado 700)')
    else:
        check('pasta de dados', True, f'{home} ainda não existe; será criada no primeiro uso')
    for path in (store.credentials_path, store.config_path, store.ledger_path):
        if path.exists():
            mode = path.stat().st_mode & 0o777
            check(f'permissão de {path.name}', mode == 0o600, f'modo {mode:o}; esperado 600')
    known = store.profiles()
    check('empresas configuradas', True, ', '.join(known) or 'nenhuma (rode `nfseai conectar`)', required=False)
    try:
        import httpx

        from .config import DEFAULT_USER_AGENT
        from .parsing import parse_login_token

        response = httpx.get(
            f'{BASE_URL}{APP_PATH}/Login', headers={'User-Agent': DEFAULT_USER_AGENT}, timeout=30,
            follow_redirects=True,
        )
        parse_login_token(response.text)
        check('portal www.nfse.gov.br', response.status_code == 200,
              f'HTTP {response.status_code}, formulário de login ok')
    except Exception as exc:  # noqa: BLE001
        check('portal www.nfse.gov.br', False, f'{type(exc).__name__}: {exc}')
    check('suporte a PDF', pdf_support_available(),
          'Playwright instalado' if pdf_support_available() else 'opcional; instale com `nfseai instalar-pdf`',
          required=False)
    ok = all(item['ok'] for item in checks if item['obrigatorio'])
    return {'ok': ok, 'versao': __version__, 'verificacoes': checks}, EXIT_OK if ok else EXIT_ERROR


def _install_certificate():
    """Instala o pacote `cryptography` no ambiente do nfseai (extra `certificado`)."""
    command = [sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', 'cryptography>=42']
    if subprocess.run([sys.executable, '-m', 'pip', '--version'], capture_output=True).returncode != 0:  # noqa: S603
        command = ['uv', 'pip', 'install', '--quiet', '--python', sys.executable, 'cryptography>=42']
    result = subprocess.run(command, capture_output=True, text=True)  # noqa: S603
    if result.returncode != 0:
        raise NfseError(f'falha ao instalar o suporte a certificado: {(result.stderr or result.stdout).strip()[-400:]}')
    return {'instalado': True, 'proximo_passo': 'rode `nfseai conectar --certificado <arquivo.pfx>` no seu terminal'}


def _install_pdf():
    """Instala o Playwright e o Chromium sem interface (headless shell) no ambiente do nfseai."""
    def execute(command):
        return subprocess.run(command, capture_output=True, text=True)  # noqa: S603

    def must(command):
        result = execute(command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-400:]
            raise NfseError(f'falha em `{" ".join(command)}`: {detail}')

    package = 'playwright>=1.49'
    if execute([sys.executable, '-m', 'pip', '--version']).returncode == 0:
        must([sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', package])
    else:
        must(['uv', 'pip', 'install', '--quiet', '--python', sys.executable, package])
    browser = [sys.executable, '-m', 'playwright', 'install']
    if execute([*browser, '--only-shell', 'chromium']).returncode != 0:
        must([*browser, 'chromium'])
    return {
        'instalado': True,
        'observacao': 'se o `nfseai pdf` reclamar de bibliotecas do sistema (Linux sem interface gráfica), rode: '
                      f'{sys.executable} -m playwright install-deps chromium (pede sudo)',
    }


def _list_notas(client, args, received=False):
    inicio = parse_date(args.inicio) if args.inicio else None
    fim = parse_date(args.fim) if args.fim else None
    if args.todas:
        if not (inicio and fim):
            raise UsageError('--todas exige --inicio e --fim')
        if received:
            raise UsageError('--todas vale para `emitidas`; em `recebidas`, use --pagina')
        return list(client.notas.iter_emitidas(inicio, fim, busca=args.busca))
    method = client.notas.recebidas if received else client.notas.emitidas
    try:
        return method(inicio, fim, busca=args.busca, pagina=args.pagina)
    except ValueError as exc:
        raise UsageError(str(exc)) from None


def _nota_view(nota):
    return {
        'chave_acesso': nota.chave_acesso,
        'numero_nfse': nota.numero_nfse,
        'situacao': nota.situacao,
        'cancelada': nota.cancelada,
        'data_geracao': nota.data_geracao,
        'valor_servico': nota.valor_servico,
        'tomador_documento': nota.tomador_documento,
        'tomador_nome': nota.tomador_nome,
        'numero_dps': nota.numero_dps,
        'serie_dps': nota.serie_dps,
        **({'secoes': nota.secoes} if hasattr(nota, 'secoes') else {'competencia': nota.competencia}),
    }


def _pdf(store, client, chave, out, ledger=None):
    nota = client.notas.get(chave)
    target = Path(out).expanduser() if out else store.private_dir(store.pdf_dir) / default_pdf_name(nota)
    path = render_nota_pdf(client, chave, target)
    if ledger is not None:
        row = ledger.by_chave(chave)
        if row:
            ledger.update(row['empresa'], row['referencia'], pdf_path=str(path))
    return {
        'arquivo': str(path),
        'bytes': path.stat().st_size,
        'chave_acesso': chave,
        'numero_nfse': nota.numero_nfse,
        'observacao': 'versão para impressão do portal; o DANFSe oficial e o XML exigem captcha no portal',
    }


def run(args):
    """Executa o comando e devolve (dados, código de saída). `dados` None não imprime nada."""
    store = Store(env_file=getattr(args, 'env_file', None))
    requested = getattr(args, 'empresa', None)
    command = {'connect': 'conectar', 'disconnect': 'desconectar'}.get(args.command, args.command)

    if command == 'conectar' and args.certificado:
        _require_tty('conectar')
        connect_certificate(
            store, Console(), args.certificado, empresa=requested, ambiente=args.ambiente,
            codigo_municipio=args.municipio, inscricao_municipal=args.inscricao_municipal or '', serie=args.serie,
            make_default=True if args.padrao else None,
        )
        return None, EXIT_OK

    if command == 'conectar':
        _require_tty('conectar')
        console = Console()

        def factory(user, password, session_file):
            return EmissorNacionalClient.from_credentials(user, password, session_file=session_file)

        summary = connect(
            store, console, factory, empresa=requested, test_login=not args.sem_teste,
            make_default=True if args.padrao else None,
        )
        empresa = summary['empresa']
        if not args.sem_configurar and store.fiscal(empresa).missing():
            console.say('')
            if console.yes_no('Quer gravar agora os padrões fiscais das notas (serviço, NBS, descrição, alíquota)?'):
                client = _client(store, empresa) if summary['login_testado'] else None
                try:
                    configure(store, console, empresa, client)
                finally:
                    if client is not None:
                        client.close()
        console.say('')
        console.say('Pronto. Confira com `nfseai status` ou volte ao seu agente de IA e peça a nota.')
        return None, EXIT_OK

    if command == 'status':
        return _status(store, args)
    if command == 'empresas':
        return {
            'empresa_padrao': store.default_profile(),
            'empresas': [_empresa_view(store, name) for name in store.profiles()],
        }, EXIT_OK
    if command == 'usar':
        return {'empresa_padrao': store.set_default(args.nome)}, EXIT_OK
    if command == 'doctor':
        return _doctor(store)
    if command == 'instalar-pdf':
        return _install_pdf(), EXIT_OK
    if command == 'instalar-certificado':
        return _install_certificate(), EXIT_OK

    empresa = store.resolve(requested)
    transporte = store.transporte(empresa)
    if transporte == TRANSPORTE_SEFIN and (command in SO_PORTAL or getattr(args, 'pdf', None) is not None):
        raise UsageError(
            f'`nfseai {command}` existe só no caminho B (portal, login por CNPJ e senha). A empresa "{empresa}" '
            'usa o caminho A (certificado): use `nfseai nota <chave>`, `nfseai xml <chave>` e `nfseai dps <id>`.'
        )
    if transporte != TRANSPORTE_SEFIN and command in SO_SEFIN:
        raise UsageError(f'`nfseai {command}` existe só no caminho A (empresa conectada por certificado).')

    if command == 'desconectar':
        if not args.confirm:
            raise ConfirmationRequired(f'apagar a senha gravada da empresa "{empresa}" exige --confirm')
        return {'empresa': empresa, 'removida': store.remove_credentials(empresa)}, EXIT_OK

    if command == 'configurar':
        caminho_a = None
        if args.serie is not None or args.leiaute is not None:
            if transporte != TRANSPORTE_SEFIN:
                raise UsageError('--serie e --leiaute existem só no caminho A (empresa conectada por certificado).')
            leiaute = None if args.leiaute is None else ('' if args.leiaute == 'padrao' else args.leiaute)
            gravado = store.save_sefin_options(empresa, leiaute=leiaute, serie=args.serie)
            caminho_a = {
                'serie': gravado.get('serie'),
                'leiaute': gravado.get('leiaute') or _leiaute_do_ambiente(gravado['ambiente']),
            }
        flags = (
            args.codigo_servico, args.nbs, args.descricao, args.aliquota_sn, args.info_complementar,
            args.municipio_prestacao, args.nome_municipio_prestacao, args.aliquota_iss, args.aliquota_irrf,
            args.tributos_federais,
            args.tributos_estaduais, args.tributos_municipais,
        )
        if caminho_a is not None and all(flag is None for flag in flags) and not args.sem_valor_tributos:
            return {'empresa': empresa, 'caminho_a': caminho_a}, EXIT_OK
        if all(flag is None for flag in flags) and not args.sem_valor_tributos:
            if transporte == TRANSPORTE_SEFIN:
                raise UsageError(
                    'no caminho A, grave os padrões com as opções: --codigo-servico, --nbs, --descricao, '
                    '--tributos-federais, --tributos-estaduais, --tributos-municipais (e --aliquota-iss se o '
                    'município exigir)'
                )
            _require_tty('configurar')
            client = _client(store, empresa) if store.credentials_origin(empresa) else None
            try:
                configure(store, Console(), empresa, client)
            finally:
                if client is not None:
                    client.close()
            return None, EXIT_OK
        fiscal = _fiscal_from_args(store, empresa, args)
        saved = store.save_fiscal(empresa, **fiscal.as_dict(raw=True))
        faltando = saved.missing(transporte)
        data = {
            'empresa': empresa,
            'perfil_fiscal': {'completo': not faltando, 'faltando': faltando, **saved.as_dict()},
        }
        if caminho_a is not None:
            data['caminho_a'] = caminho_a
        return data, EXIT_OK if not faltando else EXIT_FISCAL_INCOMPLETE

    if command == 'registro':
        ledger = _ledger(store)
        if args.referencia:
            row = ledger.get(empresa, args.referencia)
            if not row:
                return {'empresa': empresa, 'referencia': args.referencia, 'encontrada': False}, EXIT_OK
            row['historico'] = ledger.history(empresa, args.referencia)
            return {'encontrada': True, **row}, EXIT_OK
        rows = ledger.list(None if args.todas_empresas else empresa, status=args.status, limit=args.limite)
        return {'empresa': None if args.todas_empresas else empresa, 'total': len(rows), 'emissoes': rows}, EXIT_OK
    if command == 'registro-descartar':
        return operations.discard_reference(_ledger(store), empresa, args.referencia, confirm=args.confirm), EXIT_OK

    # Daqui em diante, todo comando fala com o portal.
    request = None
    if command in ('preparar', 'emitir'):
        # Valida os dados antes de abrir a sessão, para falhar sem tocar no portal.
        request = _request_from_args(store, empresa, args)
    with _client(store, empresa) as client:
        if command == 'whoami':
            return {'empresa': empresa, **client.whoami()}, EXIT_OK
        if command == 'servicos':
            return client.consultas.buscar_servico(args.termo), EXIT_OK
        if command == 'municipios':
            return client.consultas.buscar_municipio(args.termo), EXIT_OK
        if command == 'emitidas':
            return _list_notas(client, args), EXIT_OK
        if command == 'recebidas':
            return _list_notas(client, args, received=True), EXIT_OK
        if command == 'nota':
            return _nota_view(client.notas.get(args.chave)), EXIT_OK
        if command == 'rascunhos':
            return client.rascunhos.list(), EXIT_OK
        if command == 'rascunho-excluir':
            return {'excluido': client.rascunhos.delete(args.id, confirm=args.confirm)}, EXIT_OK
        if command == 'preparar':
            prepared = client.emissao.prepare(request)
            result = {
                'ensaio': True,
                'emitida': False,
                'empresa': empresa,
                'draft_id': prepared.draft_id,
                'tomador_nome': prepared.tomador_nome,
                'valor': str(request.valor),
                'competencia': request.competencia.isoformat(),
                'resumo': prepared.resumo,
            }
            if not args.manter:
                result['rascunho_descartado'] = client.emissao.discard(prepared)
            return result, EXIT_OK
        if command == 'emitir':
            ledger = _ledger(store)
            summary = operations.emit(
                client, ledger, empresa, request, confirm=args.confirm, reemitir=args.reemitir,
                permitir_duplicidade=args.permitir_duplicidade,
            )
            if not args.confirm:
                summary['proximo_passo'] = (
                    'nada foi emitido. Mostre o resumo ao usuário e, com a autorização dele para esta nota, '
                    'repita o comando com --confirm'
                )
            elif args.pdf is not None:
                try:
                    summary['pdf'] = _pdf(store, client, summary['chave_acesso'], args.pdf or None, ledger)
                except NfseError as exc:
                    # A nota existe; o PDF pode ser gerado depois com `nfseai pdf <chave>`.
                    summary['pdf_erro'] = str(exc)
            return summary, EXIT_OK
        if command == 'cancelar-previa':
            return operations.cancel(
                client, _ledger(store), empresa, args.chave, motivo=None, justificativa=''
            ), EXIT_OK
        if command == 'cancelar':
            justificativa = (args.justificativa or '').strip()
            if not 15 <= len(justificativa) <= 255:
                raise NfseValidationError('a justificativa deve ter de 15 a 255 caracteres', ['justificativa'])
            summary = operations.cancel(
                client, _ledger(store), empresa, args.chave, motivo=MOTIVOS[args.motivo],
                justificativa=justificativa, confirm=args.confirm,
                permitir_analise_fiscal=args.permitir_analise_fiscal,
            )
            if not args.confirm:
                summary['proximo_passo'] = (
                    'nada foi cancelado. Com a autorização do usuário para este cancelamento, repita com --confirm'
                )
            return summary, EXIT_OK
        if command == 'pdf':
            return _pdf(store, client, args.chave, args.out, _ledger(store)), EXIT_OK
        if command == 'impressao':
            target = Path(args.out).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(client.notas.impressao_html(args.chave), encoding='utf-8')
            target.chmod(0o600)
            return {'arquivo': str(target), 'chave_acesso': args.chave}, EXIT_OK
        if command == 'xml':
            target = Path(args.out).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            conteudo = client.notas.download_xml(args.chave)
            target.write_bytes(conteudo)
            target.chmod(0o600)
            return {'arquivo': str(target), 'chave_acesso': args.chave, 'bytes': len(conteudo)}, EXIT_OK
        if command == 'dps':
            chave = client.notas.chave_da_dps(args.id)
            return {'id_dps': args.id, 'encontrada': bool(chave), 'chave_acesso': chave}, EXIT_OK
        if command == 'registrar':
            return operations.register_existing(
                client, _ledger(store), empresa, args.referencia.strip(), args.chave
            ), EXIT_OK
    raise UsageError(f'comando desconhecido: {args.command}')


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        data, code = run(args)
    except NotConnectedError as exc:
        print(f'erro: NotConnectedError: {exc}', file=sys.stderr)
        return EXIT_NOT_CONNECTED
    except FiscalProfileIncomplete as exc:
        print(f'erro: FiscalProfileIncomplete: {exc}', file=sys.stderr)
        return EXIT_FISCAL_INCOMPLETE
    except (UsageError, WizardAborted) as exc:
        print(f'erro: {exc}', file=sys.stderr)
        return EXIT_USAGE if isinstance(exc, UsageError) else EXIT_ERROR
    except NfseError as exc:
        print(f'erro: {type(exc).__name__}: {exc}', file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print('interrompido', file=sys.stderr)
        return 130
    if data is not None:
        _print(data)
    return code


if __name__ == '__main__':
    sys.exit(main())
