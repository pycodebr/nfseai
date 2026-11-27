"""Assistentes interativos: conectar uma empresa ao portal e gravar os padrões fiscais.

Estes assistentes são rodados pelo próprio usuário, no terminal dele. A senha é lida
sem eco, usada em uma única tentativa de login e gravada em
``~/.nfseai/credentials.json`` (modo 600). Um agente de IA não deve rodar o
`conectar` nem pedir a senha no chat.

O mesmo vale para o certificado digital do caminho A (`conectar --certificado`): a senha
do certificado é lida sem eco e o arquivo é copiado para a pasta privada do nfseai.
"""
import getpass
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .config import format_document, only_digits, validate_document
from .errors import CertificateError, NfseAuthError, NfseError, ProfileError
from .profiles import DEFAULT_PROFILE, normalize_service_code, validate_profile_name

MAX_ATTEMPTS = 3
MAX_RESULTS = 15


class WizardAborted(NfseError):
    """O assistente foi interrompido sem gravar nada."""


class Console:
    """Entrada e saída do assistente, substituíveis nos testes."""

    def __init__(self, input_fn=input, getpass_fn=getpass.getpass, out=None):
        self._input = input_fn
        self._getpass = getpass_fn
        self._out = out or sys.stdout

    def say(self, text=''):
        print(text, file=self._out, flush=True)

    def ask(self, prompt, default=None):
        suffix = f' [{default}]' if default not in (None, '') else ''
        try:
            answer = self._input(f'{prompt}{suffix}: ').strip()
        except (EOFError, KeyboardInterrupt):
            raise WizardAborted('assistente interrompido; nada foi gravado') from None
        return answer or (str(default) if default not in (None, '') else '')

    def secret(self, prompt):
        try:
            return self._getpass(f'{prompt}: ')
        except (EOFError, KeyboardInterrupt):
            raise WizardAborted('assistente interrompido; nada foi gravado') from None

    def yes_no(self, prompt, default=True):
        answer = self.ask(f'{prompt} ({"S/n" if default else "s/N"})').lower()
        if not answer:
            return default
        return answer[0] in ('s', 'y')


def _ask_valid(console, prompt, parse, default=None, error='valor inválido'):
    for _ in range(MAX_ATTEMPTS):
        answer = console.ask(prompt, default)
        try:
            return parse(answer)
        except (NfseError, ValueError, InvalidOperation) as exc:
            detail = str(exc) if isinstance(exc, NfseError) else error
            console.say(f'  {detail}. Tente de novo.')
    raise WizardAborted(f'{error}: limite de tentativas atingido; nada foi gravado')


def connect(store, console, client_factory, *, empresa=None, test_login=True, make_default=None):
    """Pergunta CNPJ e senha, testa o login uma vez e grava as credenciais da empresa.

    ``client_factory(user, password, session_file)`` devolve um cliente do portal; é
    injetado para os testes rodarem sem rede. Devolve um resumo sem a senha.
    """
    console.say('nfseai · conectar ao Emissor Nacional de NFS-e (www.nfse.gov.br)')
    console.say('')
    console.say('Você vai informar o CNPJ e a senha que usa para entrar no portal.')
    console.say(f'Eles ficam só nesta máquina, em {store.credentials_path} (acesso restrito ao seu usuário),')
    console.say('e são enviados apenas ao portal oficial, para fazer o login.')
    console.say('')
    if empresa:
        name = validate_profile_name(empresa)
    else:
        known = store.profiles()
        suggestion = store.default_profile() or (known[0] if len(known) == 1 else DEFAULT_PROFILE)
        name = _ask_valid(
            console, 'Apelido desta empresa (letras minúsculas, números e "-")', validate_profile_name,
            default=suggestion, error='apelido inválido',
        )
    if store.credentials_origin(name) == 'arquivo' and not console.yes_no(
        f'A empresa "{name}" já tem credenciais gravadas. Substituir?', default=True
    ):
        raise WizardAborted('credenciais mantidas; nada foi alterado')
    user = _ask_valid(console, 'CNPJ (ou CPF) do login no portal', validate_document, error='CNPJ/CPF inválido')
    password = console.secret('Senha do portal (não aparece enquanto você digita)')
    if not password:
        raise WizardAborted('senha vazia; nada foi gravado')
    identity = None
    session_file = store.session_file(name)
    if test_login:
        console.say('')
        console.say('Testando o login no portal (uma única tentativa, por precaução contra bloqueio da conta)...')
        store.ensure_home()
        store.private_dir(session_file.parent)
        client = client_factory(user, password, session_file)
        failure = None
        try:
            identity = client.whoami()
        except NfseAuthError as exc:
            failure = (
                f'o portal recusou o login: {exc}. Nada foi gravado. Confira o CNPJ e a senha entrando em '
                'https://www.nfse.gov.br/EmissorNacional pelo navegador e rode `nfseai conectar` de novo.'
            )
        except NfseError as exc:
            failure = (
                f'não foi possível falar com o portal agora ({exc}). Nada foi gravado. Tente de novo em alguns '
                'minutos ou use `nfseai conectar --sem-teste` para gravar sem testar.'
            )
        finally:
            client.close()
        if failure:
            _drop(session_file)
            raise WizardAborted(failure)
    store.save_credentials(name, user, password, make_default=make_default)
    console.say('')
    if identity:
        console.say(f'Login confirmado: {identity.get("nome") or "contribuinte"} '
                    f'({format_document(identity["inscricao"])}).')
    console.say(f'Empresa "{name}" conectada. Credenciais gravadas em {store.credentials_path} (modo 600).')
    return {
        'empresa': name,
        'login': format_document(user),
        'contribuinte': (identity or {}).get('nome'),
        'login_testado': bool(identity),
        'padrao': store.default_profile() == name,
    }


def _parse_ibge(text):
    digits = only_digits(text)
    if len(digits) != 7 or digits != text.strip():
        raise ProfileError('o código IBGE do município tem 7 dígitos (ex.: 3550308)')
    return digits


def connect_certificate(store, console, arquivo, *, empresa=None, ambiente='producao-restrita',
                        codigo_municipio=None, inscricao_municipal='', serie=1, make_default=None):
    """Conecta uma empresa pelo caminho A: abre o certificado A1, mostra a empresa e grava após confirmação.

    O ambiente padrão é a produção restrita (homologação, notas sem valor fiscal). Emitir em produção exige
    escolher ``ambiente='producao'`` de forma explícita. Devolve um resumo sem a senha.
    """
    from .certificado import CertificadoA1

    console.say('nfseai · conectar pelo certificado digital (API oficial da Sefin Nacional)')
    console.say('')
    console.say('Você vai informar a senha do certificado A1 (.pfx) da empresa. O arquivo é copiado para')
    console.say(f'{store.home / "certificados"} e a senha fica em {store.credentials_path}, com acesso restrito ao')
    console.say('seu usuário. Os dois só são usados para assinar as notas e abrir a conexão com a Sefin.')
    console.say('')
    caminho = Path(arquivo).expanduser()
    if not caminho.is_file():
        raise WizardAborted(f'arquivo do certificado não encontrado: {caminho}; nada foi gravado')
    if empresa:
        name = validate_profile_name(empresa)
    else:
        name = _ask_valid(
            console, 'Apelido desta empresa (letras minúsculas, números e "-")', validate_profile_name,
            default=store.default_profile() or DEFAULT_PROFILE, error='apelido inválido',
        )
    if store.certificate_origin(name) == 'arquivo' and not console.yes_no(
        f'A empresa "{name}" já tem um certificado gravado. Substituir?', default=True
    ):
        raise WizardAborted('certificado mantido; nada foi alterado')
    senha = console.secret('Senha do certificado (não aparece enquanto você digita)')
    if not senha:
        raise WizardAborted('senha vazia; nada foi gravado')
    try:
        certificado = CertificadoA1.carregar(caminho, senha)
    except CertificateError as exc:
        raise WizardAborted(f'{exc}. Nada foi gravado.') from None
    console.say('')
    console.say(f'Certificado de: {certificado.titular}')
    console.say(f'CNPJ: {format_document(certificado.cnpj) if certificado.cnpj else "não encontrado"}')
    console.say(f'Válido até: {certificado.valido_ate:%d/%m/%Y}')
    console.say(f'Ambiente: {ambiente}' + (' (homologação, notas sem valor fiscal)' if ambiente != 'producao' else ''))
    console.say('')
    if not codigo_municipio:
        codigo_municipio = _ask_valid(
            console, 'Código IBGE do município emissor da empresa (7 dígitos)', _parse_ibge,
            error='código IBGE inválido',
        )
    if not console.yes_no('É esta a empresa que vai emitir as notas?', default=True):
        raise WizardAborted('conexão cancelada; nada foi gravado')
    try:
        resumo = store.save_certificate(
            name, caminho, senha, ambiente=ambiente, codigo_municipio=codigo_municipio,
            inscricao_municipal=inscricao_municipal or '', serie=serie, make_default=make_default,
        )
    except (CertificateError, ProfileError) as exc:
        raise WizardAborted(f'{exc}. Nada foi gravado.') from None
    console.say(f'Empresa "{name}" conectada pelo certificado digital.')
    if not inscricao_municipal:
        console.say('Se o município exigir a inscrição municipal na nota, rode de novo com --inscricao-municipal.')
    console.say('Próximo passo: grave os padrões fiscais com `nfseai configurar --codigo-servico ... --nbs ... '
                '--descricao "..." --tributos-federais ... --tributos-estaduais ... --tributos-municipais ...`.')
    return resumo


def _drop(path):
    if path.exists():
        path.unlink()


def _pick(console, results, label):
    """Mostra resultados numerados e devolve o escolhido, ou None para pesquisar de novo."""
    shown = results[:MAX_RESULTS]
    for position, item in enumerate(shown, start=1):
        console.say(f'  {position}) {item.get("text") or item.get("id")}')
    if len(results) > MAX_RESULTS:
        console.say(f'  ... e mais {len(results) - MAX_RESULTS}. Refine a pesquisa se o seu não apareceu.')
    answer = console.ask(f'Número do {label} (ou Enter para pesquisar de novo)')
    if not answer.isdigit() or not 1 <= int(answer) <= len(shown):
        return None
    return shown[int(answer) - 1]


def _ask_service(console, client, current):
    for _ in range(MAX_ATTEMPTS * 2):
        answer = console.ask(
            'Código de tributação nacional do serviço (ex.: 08.02.01) ou uma palavra para pesquisar',
            current or None,
        )
        if not answer:
            continue
        if len(only_digits(answer)) == 6 and not any(ch.isalpha() for ch in answer):
            code = normalize_service_code(answer)
            if client is None:
                return code
            try:
                found = [item for item in client.consultas.buscar_servico(code) if item.get('id') == code]
            except NfseError as exc:
                console.say(f'  Não foi possível conferir o código no portal ({exc}); ele será validado no ensaio.')
                return code
            if found:
                console.say(f'  {found[0].get("text")}')
                return code
            console.say('  O portal não reconhece esse código. Confira com o contador ou pesquise por uma palavra.')
            continue
        if client is None:
            console.say('  Sem conexão com o portal para pesquisar: informe o código no formato 00.00.00.')
            continue
        try:
            results = client.consultas.buscar_servico(answer)
        except NfseError as exc:
            console.say(f'  A pesquisa falhou ({exc}). Informe o código no formato 00.00.00.')
            continue
        if not results:
            console.say('  Nenhum serviço encontrado. Tente outra palavra (ex.: "treinamento", "software").')
            continue
        chosen = _pick(console, results, 'serviço')
        if chosen:
            return normalize_service_code(chosen['id'])
    raise WizardAborted('código do serviço não definido; nada foi gravado')


def _parse_nbs(text):
    digits = only_digits(text)
    if len(digits) != 9:
        raise ProfileError('o código NBS tem 9 dígitos (ex.: 1.2205.19.00 ou 122051900)')
    return digits


def _parse_description(text):
    text = text.strip()
    if not 1 <= len(text) <= 2000:
        raise ProfileError('a descrição deve ter de 1 a 2000 caracteres')
    return text


def _parse_rate(text):
    value = Decimal(text.replace('%', '').replace(',', '.').strip())
    if not Decimal('0') < value < Decimal('100'):
        raise ProfileError('a alíquota deve ficar entre 0 e 100')
    return value.quantize(Decimal('0.01'))


def _ask_city(console, client):
    """Município de prestação diferente do município da empresa: código IBGE e nome do portal."""
    for _ in range(MAX_ATTEMPTS * 2):
        term = console.ask('Nome do município onde o serviço é prestado')
        if not term:
            continue
        if client is None:
            code = console.ask('Código IBGE do município (7 dígitos)')
            if len(only_digits(code)) == 7:
                return only_digits(code), term
            console.say('  Código IBGE inválido.')
            continue
        try:
            results = client.consultas.buscar_municipio(term)
        except NfseError as exc:
            console.say(f'  A pesquisa falhou ({exc}).')
            continue
        if not results:
            console.say('  Nenhum município encontrado. Confira a grafia.')
            continue
        chosen = _pick(console, results, 'município')
        if chosen:
            return str(chosen['id']), chosen.get('text') or term
    raise WizardAborted('município de prestação não definido; nada foi gravado')


def configure(store, console, name, client=None):
    """Pergunta os padrões fiscais das notas da empresa e grava em ``config.json``."""
    current = store.fiscal(name)
    console.say('')
    console.say(f'Padrões fiscais das notas da empresa "{name}"')
    console.say('Esses dados vêm do enquadramento fiscal da empresa e valem para toda nota que não informar')
    console.say('outro valor. Em caso de dúvida, confirme com o seu contador antes de emitir.')
    console.say('')
    code = _ask_service(console, client, current.codigo_tributacao_nacional)
    nbs = _ask_valid(
        console, 'Código NBS do serviço (9 dígitos, ex.: 122051900)', _parse_nbs,
        default=current.codigo_nbs or None, error='código NBS inválido',
    )
    description = _ask_valid(
        console, 'Descrição padrão do serviço na nota', _parse_description,
        default=current.descricao or None, error='descrição inválida',
    )
    console.say('')
    console.say('Valor aproximado dos tributos (campo "Total dos tributos" da nota):')
    console.say('  1) Informar a alíquota do Simples Nacional')
    console.say('  2) Não informar')
    option = console.ask('Opção', '2' if current.tipo_valor_tributos == 3 else '1')
    rate, tipo = None, 3
    if option.strip() != '2':
        tipo = 4
        rate = _ask_valid(
            console, 'Alíquota do Simples Nacional, em % (ex.: 6,00)', _parse_rate,
            default=str(current.aliquota_sn).replace('.', ',') if current.aliquota_sn is not None else None,
            error='alíquota inválida',
        )
    city_code, city_name = '', ''
    same_city = not current.codigo_municipio_prestacao
    if not console.yes_no('O serviço é prestado no município da própria empresa?', default=same_city):
        city_code, city_name = _ask_city(console, client)
    saved = store.save_fiscal(
        name,
        codigo_tributacao_nacional=code,
        codigo_nbs=nbs,
        descricao=description,
        tipo_valor_tributos=tipo,
        aliquota_sn=rate if tipo == 4 else current.aliquota_sn,
        codigo_municipio_prestacao=city_code,
        nome_municipio_prestacao=city_name,
    )
    console.say('')
    console.say(f'Padrões gravados em {store.config_path}.')
    console.say('Antes da primeira nota, faça um ensaio (monta o rascunho no portal, confere e descarta):')
    console.say('  nfseai preparar --documento <CPF ou CNPJ do cliente> --valor 10,00')
    return saved


__all__ = ['Console', 'WizardAborted', 'configure', 'connect', 'connect_certificate']
