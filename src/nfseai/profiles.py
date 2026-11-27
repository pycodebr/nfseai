"""Empresas (perfis), credenciais e padrões fiscais guardados na máquina do usuário.

Tudo fica em ``~/.nfseai`` (ou em ``NFSEAI_HOME``), com a pasta em modo 700 e os
arquivos em modo 600:

- ``config.json``: empresa padrão e os padrões fiscais de cada empresa (sem segredo);
- ``credentials.json``: CNPJ/CPF e senha do portal de cada empresa;
- ``sessions/<empresa>.json``: cookies da sessão do portal;
- ``ledger.sqlite3``: registro local das emissões;
- ``debug/``: respostas do portal nas emissões e nos cancelamentos;
- ``pdf/``: PDFs gerados sem caminho de saída;
- ``certificados/<empresa>.pfx``: certificado A1 das empresas do caminho A (API da Sefin Nacional);
- ``tls/``: pasta onde a chave do certificado fica por um instante ao abrir a conexão (apagada em seguida).

As credenciais também podem vir do ambiente, para servidores e contêineres:
``NFSEAI_<EMPRESA>_USER`` e ``NFSEAI_<EMPRESA>_PASSWORD`` para uma empresa específica,
ou ``NFSEAI_USER`` e ``NFSEAI_PASSWORD`` para a empresa em uso. No caminho A, o certificado vem de
``NFSEAI_<EMPRESA>_CERT`` (caminho do .pfx) e ``NFSEAI_<EMPRESA>_CERT_PASSWORD``, ou de ``NFSEAI_CERT`` e
``NFSEAI_CERT_PASSWORD``. O ambiente tem precedência sobre o arquivo. Nenhuma senha é impressa nem incluída
em mensagens.
"""
import json
import os
import re
import tempfile
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path

from .config import Credentials, only_digits
from .errors import FiscalProfileIncomplete, NotConnectedError, ProfileError
from .models import Servico, Tributacao, TributosAproximados

HOME_ENV = 'NFSEAI_HOME'
PROFILE_ENV = 'NFSEAI_EMPRESA'
ENV_FILE_ENV = 'NFSEAI_ENV_FILE'
DEFAULT_PROFILE = 'principal'
PROFILE_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,31}')

ORIGEM_AMBIENTE = 'ambiente'
ORIGEM_ARQUIVO = 'arquivo'

SERVICO_KEYS = (
    'codigo_tributacao_nacional',
    'codigo_nbs',
    'descricao',
    'codigo_municipio_prestacao',
    'nome_municipio_prestacao',
    'informacoes_complementares',
)
TRIBUTACAO_KEYS = (
    'tipo_valor_tributos', 'aliquota_sn', 'aliquota_iss', 'tributos_federais', 'tributos_estaduais',
    'tributos_municipais', 'aliquota_irrf',
)
DECIMAL_KEYS = (
    'aliquota_sn', 'aliquota_iss', 'tributos_federais', 'tributos_estaduais', 'tributos_municipais', 'aliquota_irrf',
)
IRRF_DISPENSADO_ATE = Decimal('10.00')  # Lei 9.430/1996, art. 67
TRANSPORTE_PORTAL = 'portal'
TRANSPORTE_SEFIN = 'sefin'
AMBIENTES_SEFIN = ('producao-restrita', 'producao')
FISCAL_LABELS = {
    'codigo_tributacao_nacional': 'código de tributação nacional do serviço (--codigo-servico)',
    'codigo_nbs': 'código NBS (--nbs)',
    'descricao': 'descrição do serviço (--descricao)',
    'aliquota_sn': 'alíquota do Simples Nacional (--aliquota-sn) ou --sem-valor-tributos',
    'tributos_aproximados': 'percentuais aproximados dos tributos (--tributos-federais, --tributos-estaduais e '
                            '--tributos-municipais)',
}


def home_dir():
    """Pasta de dados do nfseai: ``NFSEAI_HOME`` ou ``~/.nfseai``."""
    custom = os.environ.get(HOME_ENV)
    return Path(custom).expanduser() if custom else Path.home() / '.nfseai'


def validate_profile_name(name):
    name = (name or '').strip().lower()
    if not PROFILE_RE.fullmatch(name):
        raise ProfileError(
            f'nome de empresa inválido: {name!r}. Use de 1 a 32 caracteres: letras minúsculas, números, '
            '"-" e "_", começando por letra ou número (ex.: "minha-empresa").'
        )
    return name


def env_prefix(name):
    return 'NFSEAI_' + re.sub(r'[^A-Z0-9]', '_', name.upper())


def _write_private(path, text):
    """Grava o arquivo com modo 600 desde a criação, por troca atômica."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read_json(path):
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise ProfileError(f'arquivo corrompido: {path} ({exc}). Corrija ou remova o arquivo.') from None
    if not isinstance(data, dict):
        raise ProfileError(f'arquivo com formato inesperado: {path}')
    return data


def _decimal(value, label):
    try:
        return Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise ProfileError(f'{label} inválido: {value!r}') from None


@dataclass(frozen=True)
class FiscalDefaults:
    """Padrões fiscais de uma empresa, usados quando a emissão não informa o campo."""

    codigo_tributacao_nacional: str = ''
    codigo_nbs: str = ''
    descricao: str = ''
    codigo_municipio_prestacao: str = ''
    nome_municipio_prestacao: str = ''
    informacoes_complementares: str = ''
    tipo_valor_tributos: int = 4
    aliquota_sn: Decimal | None = None
    aliquota_iss: Decimal | None = None
    tributos_federais: Decimal | None = None
    tributos_estaduais: Decimal | None = None
    tributos_municipais: Decimal | None = None
    aliquota_irrf: Decimal | None = None

    @property
    def tributos_aproximados(self):
        valores = (self.tributos_federais, self.tributos_estaduais, self.tributos_municipais)
        if any(valor is None for valor in valores):
            return None
        return TributosAproximados(*valores)

    def missing(self, transporte=TRANSPORTE_PORTAL):
        """Campos obrigatórios que ainda não têm valor, conforme o caminho da empresa."""
        obrigatorios = ('codigo_tributacao_nacional', 'codigo_nbs', 'descricao')
        if transporte == TRANSPORTE_SEFIN:
            # O grupo cNBS é opcional na DPS 1.01; a Sefin aceita a nota sem NBS.
            obrigatorios = ('codigo_tributacao_nacional', 'descricao')
        faltando = [key for key in obrigatorios if not getattr(self, key)]
        if transporte == TRANSPORTE_SEFIN:
            if self.tributos_aproximados is None:
                faltando.append('tributos_aproximados')
        elif self.tipo_valor_tributos == 4 and self.aliquota_sn is None:
            faltando.append('aliquota_sn')
        return faltando

    def require_complete(self, transporte=TRANSPORTE_PORTAL):
        faltando = self.missing(transporte)
        if faltando:
            detalhes = '; '.join(FISCAL_LABELS[key] for key in faltando)
            raise FiscalProfileIncomplete(
                f'faltam dados fiscais para montar a nota: {detalhes}. Informe na linha de comando ou grave os '
                'padrões da empresa com `nfseai configurar`.',
                faltando,
            )

    def servico(self, transporte=TRANSPORTE_PORTAL):
        self.require_complete(transporte)
        return Servico(
            codigo_tributacao_nacional=self.codigo_tributacao_nacional,
            codigo_nbs=self.codigo_nbs,
            descricao=self.descricao,
            codigo_municipio_prestacao=self.codigo_municipio_prestacao,
            nome_municipio_prestacao=self.nome_municipio_prestacao,
            informacoes_complementares=self.informacoes_complementares,
        )

    def tributacao(self, transporte=TRANSPORTE_PORTAL, *, reter_issqn=False, valor=None, reter_irrf=True):
        """Tributação da nota. No caminho A, com `aliquota_irrf` gravada e o `valor` do serviço, calcula o IRRF
        retido truncando nos centavos (R$ 1.234,50 x 1,5% = 18,5175 -> R$ 18,51).
        Valor igual ou inferior a R$ 10,00 não é retido (Lei 9.430/1996, art. 67). `reter_irrf=False` emite a
        nota sem a retenção."""
        self.require_complete(transporte)
        if transporte == TRANSPORTE_SEFIN:
            irrf = None
            if reter_irrf and self.aliquota_irrf is not None and valor is not None:
                irrf = (Decimal(valor) * self.aliquota_irrf / 100).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
                if irrf <= IRRF_DISPENSADO_ATE:
                    irrf = None
            return Tributacao(
                aliquota_iss=self.aliquota_iss, tributos_aproximados=self.tributos_aproximados,
                reter_issqn=reter_issqn, retencao_irrf=irrf,
            )
        return Tributacao(
            tipo_valor_tributos=self.tipo_valor_tributos, aliquota_sn=self.aliquota_sn, reter_issqn=reter_issqn
        )

    def merged(self, **overrides):
        """Cópia com os campos informados (valores None são ignorados)."""
        data = self.as_dict(raw=True)
        data.update({key: value for key, value in overrides.items() if value is not None})
        return FiscalDefaults(**data)

    def as_dict(self, raw=False):
        data = {key: getattr(self, key) for key in (*SERVICO_KEYS, *TRIBUTACAO_KEYS)}
        if not raw:
            for key in DECIMAL_KEYS:
                data[key] = str(data[key]) if data[key] is not None else None
        return data


class Store:
    """Leitura e gravação das empresas, credenciais e padrões fiscais."""

    def __init__(self, home=None, env_file=None):
        self.home = Path(home).expanduser() if home else home_dir()
        self._env_file = env_file

    # ------------------------------------------------------------------ caminhos
    @property
    def env_file(self):
        return self._env_file

    @property
    def config_path(self):
        return self.home / 'config.json'

    @property
    def credentials_path(self):
        return self.home / 'credentials.json'

    @property
    def ledger_path(self):
        return self.home / 'ledger.sqlite3'

    @property
    def debug_dir(self):
        return self.home / 'debug'

    @property
    def pdf_dir(self):
        return self.home / 'pdf'

    def session_file(self, name):
        return self.home / 'sessions' / f'{name}.json'

    def certificate_path(self, name):
        return self.home / 'certificados' / f'{name}.pfx'

    @property
    def tls_dir(self):
        return self.home / 'tls'

    def ensure_home(self):
        self.home.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        return self.home

    def private_dir(self, path):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
        return path

    # ------------------------------------------------------------------ ambiente
    def _environment(self):
        """Variáveis do processo, completadas pelo arquivo dotenv quando informado."""
        values = {}
        candidate = self._env_file or os.environ.get(ENV_FILE_ENV)
        if candidate:
            path = Path(candidate).expanduser()
            if not path.is_file():
                raise ProfileError(f'arquivo de ambiente não encontrado: {path}')
            from dotenv import dotenv_values

            values = {key: value for key, value in dotenv_values(path).items() if value is not None}
        values.update(os.environ)
        return values

    def _env_credentials(self, name):
        env = self._environment()
        prefix = env_prefix(name)
        for user_key, pass_key in ((f'{prefix}_USER', f'{prefix}_PASSWORD'), ('NFSEAI_USER', 'NFSEAI_PASSWORD')):
            user, password = env.get(user_key), env.get(pass_key)
            if user and password:
                return Credentials(user=user.strip(), password=password)
            if user or password:
                missing = pass_key if user else user_key
                raise NotConnectedError(f'credenciais incompletas no ambiente: falta {missing}')
        return None

    # ------------------------------------------------------------------ empresas
    def load_config(self):
        config = _read_json(self.config_path)
        config.setdefault('padrao', None)
        config.setdefault('empresas', {})
        return config

    def save_config(self, config):
        self.ensure_home()
        _write_private(self.config_path, json.dumps(config, ensure_ascii=False, indent=2) + '\n')

    def _stored_credentials(self):
        return _read_json(self.credentials_path)

    def profiles(self):
        """Empresas conhecidas: com credenciais gravadas ou com padrões fiscais."""
        names = set(self.load_config()['empresas']) | set(self._stored_credentials())
        return sorted(names)

    def default_profile(self):
        return self.load_config().get('padrao')

    def set_default(self, name):
        name = validate_profile_name(name)
        if name not in self.profiles():
            raise ProfileError(f'empresa desconhecida: {name!r} (conhecidas: {self.profiles() or "nenhuma"})')
        config = self.load_config()
        config['padrao'] = name
        self.save_config(config)
        return name

    def resolve(self, name=None):
        """Empresa a usar: a informada, ``NFSEAI_EMPRESA``, a padrão, a única existente ou "principal"."""
        if name:
            return validate_profile_name(name)
        from_env = os.environ.get(PROFILE_ENV)
        if from_env:
            return validate_profile_name(from_env)
        default = self.default_profile()
        if default:
            return default
        known = self.profiles()
        if len(known) == 1:
            return known[0]
        if len(known) > 1:
            raise ProfileError(
                f'há mais de uma empresa configurada ({", ".join(known)}): informe --empresa ou defina a padrão '
                'com `nfseai usar <empresa>`.'
            )
        return DEFAULT_PROFILE

    # --------------------------------------------------------------- credenciais
    def credentials_origin(self, name):
        """'ambiente', 'arquivo' ou None, sem ler a senha para fora."""
        if self._env_credentials(name) is not None:
            return ORIGEM_AMBIENTE
        entry = self._stored_credentials().get(name) or {}
        if entry.get('user') and entry.get('password'):
            return ORIGEM_ARQUIVO
        return None

    def load_credentials(self, name):
        """Credenciais da empresa: ambiente primeiro, depois o arquivo. Nunca imprime valores."""
        from_env = self._env_credentials(name)
        if from_env is not None:
            return from_env
        entry = self._stored_credentials().get(name) or {}
        if entry.get('user') and entry.get('password'):
            return Credentials(user=entry['user'], password=entry['password'])
        raise NotConnectedError(
            f'a empresa "{name}" ainda não está conectada ao portal. Peça ao usuário para rodar '
            f'`nfseai conectar{"" if name == DEFAULT_PROFILE else " --empresa " + name}` no terminal dele; '
            'a senha é digitada lá, nunca no chat.'
        )

    def login_user(self, name):
        """CNPJ/CPF de login (só dígitos) ou None; não devolve a senha."""
        try:
            return self.load_credentials(name).inscricao
        except NotConnectedError:
            return None

    def save_credentials(self, name, user, password, *, make_default=None):
        name = validate_profile_name(name)
        if not only_digits(user) or not password:
            raise ProfileError('CNPJ/CPF e senha são obrigatórios')
        self.ensure_home()
        stored = self._stored_credentials()
        stored[name] = {'user': only_digits(user), 'password': password}
        _write_private(self.credentials_path, json.dumps(stored, ensure_ascii=False, indent=2) + '\n')
        config = self.load_config()
        config['empresas'].setdefault(name, {})
        if make_default or (make_default is None and not config.get('padrao')):
            config['padrao'] = name
        self.save_config(config)
        return name

    def remove_credentials(self, name):
        """Apaga a senha gravada e a sessão da empresa. Os padrões fiscais ficam."""
        name = validate_profile_name(name)
        stored = self._stored_credentials()
        removed = stored.pop(name, None) is not None
        if removed:
            _write_private(self.credentials_path, json.dumps(stored, ensure_ascii=False, indent=2) + '\n')
        for path in (self.session_file(name), self.certificate_path(name)):
            if path.exists():
                path.unlink()
                removed = True
        return removed

    # ------------------------------------------------------------ padrões fiscais
    def fiscal(self, name):
        entry = self.load_config()['empresas'].get(name) or {}
        servico = entry.get('servico') or {}
        tributacao = entry.get('tributacao') or {}
        decimais = {
            key: _decimal(tributacao.get(key), key) if tributacao.get(key) not in (None, '') else None
            for key in DECIMAL_KEYS
        }
        return FiscalDefaults(
            **{key: str(servico.get(key) or '') for key in SERVICO_KEYS},
            tipo_valor_tributos=int(tributacao.get('tipo_valor_tributos') or 4),
            **decimais,
        )

    def save_fiscal(self, name, **fields):
        """Atualiza só os campos informados (None mantém o valor atual) e valida o conjunto."""
        name = validate_profile_name(name)
        unknown = set(fields) - set(SERVICO_KEYS) - set(TRIBUTACAO_KEYS)
        if unknown:
            raise ProfileError(f'campos fiscais desconhecidos: {sorted(unknown)}')
        updated = self.fiscal(name).merged(**fields)
        validate_fiscal(updated)
        config = self.load_config()
        entry = config['empresas'].setdefault(name, {})
        entry['servico'] = {key: getattr(updated, key) for key in SERVICO_KEYS}
        entry['tributacao'] = {
            'tipo_valor_tributos': updated.tipo_valor_tributos,
            'aliquota_sn': str(updated.aliquota_sn) if updated.aliquota_sn is not None else None,
        }
        # Campos do caminho A só entram no arquivo quando informados (o formato do caminho B não muda).
        for key in DECIMAL_KEYS[1:]:
            if getattr(updated, key) is not None:
                entry['tributacao'][key] = str(getattr(updated, key))
        if not config.get('padrao'):
            config['padrao'] = name
        self.save_config(config)
        return updated

    # ------------------------------------------------------- caminho A (certificado)
    def transporte(self, name):
        """'sefin' para empresa conectada por certificado (caminho A); 'portal' nos demais casos."""
        entry = self.load_config()['empresas'].get(name) or {}
        return entry.get('transporte') or TRANSPORTE_PORTAL

    def sefin_config(self, name):
        entry = self.load_config()['empresas'].get(name) or {}
        config = entry.get('sefin')
        if not config:
            raise ProfileError(f'a empresa "{name}" não está conectada pelo caminho A (certificado)')
        return dict(config)

    def save_sefin_options(self, name, *, leiaute=None, serie=None):
        """Opções do caminho A. ``leiaute`` força o pacote de esquemas (para a virada da produção para a NT 009);
        ``''`` volta ao padrão do ambiente. ``serie`` troca a série da DPS: a numeração da série nova começa no
        registro local e a da série anterior fica guardada."""
        from .dps import LEIAUTES

        config = self.load_config()
        entry = (config['empresas'].get(name) or {}).get('sefin')
        if not entry:
            raise ProfileError(f'a empresa "{name}" não está conectada pelo caminho A (certificado)')
        if serie is not None:
            if not 1 <= int(serie) <= 49999:
                raise ProfileError('a série da DPS de aplicativo próprio vai de 1 a 49999')
            entry['serie'] = int(serie)
        if leiaute is not None:
            if leiaute and leiaute not in LEIAUTES:
                raise ProfileError(f'leiaute desconhecido: {leiaute!r} (use {", ".join(LEIAUTES)})')
            if leiaute:
                entry['leiaute'] = leiaute
            else:
                entry.pop('leiaute', None)
        self.save_config(config)
        return dict(entry)

    def emitente(self, name):
        from .dps import Emitente

        config = self.sefin_config(name)
        return Emitente(
            cnpj=config['cnpj'], codigo_municipio=config['codigo_municipio'],
            inscricao_municipal=config.get('inscricao_municipal') or '',
        )

    def _env_certificate(self, name):
        env = self._environment()
        prefix = env_prefix(name)
        pairs = ((f'{prefix}_CERT', f'{prefix}_CERT_PASSWORD'), ('NFSEAI_CERT', 'NFSEAI_CERT_PASSWORD'))
        for path_key, pass_key in pairs:
            path, password = env.get(path_key), env.get(pass_key)
            if path and password:
                return Path(path).expanduser(), password
            if path or password:
                missing = pass_key if path else path_key
                raise NotConnectedError(f'certificado incompleto no ambiente: falta {missing}')
        return None

    def certificate_origin(self, name):
        """'ambiente', 'arquivo' ou None, sem ler a senha para fora."""
        if self._env_certificate(name) is not None:
            return ORIGEM_AMBIENTE
        entry = self._stored_credentials().get(name) or {}
        if entry.get('certificado_senha') and self.certificate_path(name).is_file():
            return ORIGEM_ARQUIVO
        return None

    def load_certificate(self, name):
        """Certificado A1 da empresa: ambiente primeiro, depois o arquivo. Nunca imprime a senha."""
        from .certificado import CertificadoA1

        from_env = self._env_certificate(name)
        if from_env is not None:
            path, password = from_env
            if not path.is_file():
                raise NotConnectedError(f'arquivo do certificado não encontrado: {path}')
            return CertificadoA1.carregar(path, password)
        entry = self._stored_credentials().get(name) or {}
        if entry.get('certificado_senha') and self.certificate_path(name).is_file():
            return CertificadoA1.carregar(self.certificate_path(name), entry['certificado_senha'])
        raise NotConnectedError(
            f'a empresa "{name}" não tem certificado digital conectado. Peça ao usuário para rodar '
            f'`nfseai conectar --certificado <arquivo.pfx>{"" if name == DEFAULT_PROFILE else " --empresa " + name}` '
            'no terminal dele; a senha do certificado é digitada lá, nunca no chat.'
        )

    def save_certificate(self, name, pfx, password, *, ambiente, codigo_municipio, inscricao_municipal='',
                         serie=1, make_default=None):
        """Confere e grava o certificado A1 da empresa e os dados do emitente no caminho A.

        Nada é gravado se a senha estiver errada, se o certificado estiver vencido ou se não trouxer o CNPJ.
        Devolve um resumo sem a senha.
        """
        from .certificado import CertificadoA1

        name = validate_profile_name(name)
        if ambiente not in AMBIENTES_SEFIN:
            raise ProfileError(f'ambiente inválido: {ambiente!r} (use {" ou ".join(AMBIENTES_SEFIN)})')
        if not re.fullmatch(r'\d{7}', (codigo_municipio or '').strip()):
            raise ProfileError('o município emissor é o código IBGE de 7 dígitos')
        if not 1 <= int(serie) <= 49999:
            raise ProfileError('a série da DPS de aplicativo próprio vai de 1 a 49999')
        dados = pfx if isinstance(pfx, bytes) else Path(pfx).expanduser().read_bytes()
        certificado = CertificadoA1.carregar(dados, password)
        certificado.conferir(certificado.cnpj or '0')
        self.ensure_home()
        self.private_dir(self.certificate_path(name).parent)
        _write_private_bytes(self.certificate_path(name), dados)
        stored = self._stored_credentials()
        stored.setdefault(name, {})['certificado_senha'] = password
        _write_private(self.credentials_path, json.dumps(stored, ensure_ascii=False, indent=2) + '\n')
        config = self.load_config()
        entry = config['empresas'].setdefault(name, {})
        entry['transporte'] = TRANSPORTE_SEFIN
        entry['sefin'] = {
            'cnpj': certificado.cnpj,
            'ambiente': ambiente,
            'codigo_municipio': codigo_municipio.strip(),
            'inscricao_municipal': (inscricao_municipal or '').strip(),
            'serie': int(serie),
        }
        if make_default or (make_default is None and not config.get('padrao')):
            config['padrao'] = name
        self.save_config(config)
        return {
            'empresa': name,
            'cnpj': certificado.cnpj,
            'titular': certificado.titular,
            'valido_ate': certificado.valido_ate.date().isoformat(),
            'ambiente': ambiente,
            'padrao': config.get('padrao') == name,
        }


def _write_private_bytes(path, data):
    """Como `_write_private`, para conteúdo binário (o .pfx)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def validate_fiscal(fiscal):
    """Confere o formato dos padrões fiscais informados (não consulta o portal)."""
    code = fiscal.codigo_tributacao_nacional
    if code and not re.fullmatch(r'\d{2}\.\d{2}\.\d{2}', code):
        raise ProfileError(f'código de tributação nacional deve ter o formato 00.00.00 (recebido: {code!r})')
    if fiscal.codigo_nbs and not re.fullmatch(r'\d{9}', fiscal.codigo_nbs):
        raise ProfileError(f'código NBS deve ter 9 dígitos, sem pontos (recebido: {fiscal.codigo_nbs!r})')
    if len(fiscal.descricao) > 2000:
        raise ProfileError('a descrição do serviço aceita até 2000 caracteres')
    if len(fiscal.informacoes_complementares) > 2000:
        raise ProfileError('as informações complementares aceitam até 2000 caracteres')
    if fiscal.codigo_municipio_prestacao and not re.fullmatch(r'\d{7}', fiscal.codigo_municipio_prestacao):
        raise ProfileError('o município de prestação é o código IBGE de 7 dígitos')
    if fiscal.tipo_valor_tributos not in (3, 4):
        raise ProfileError('valor aproximado dos tributos: use 4 (informar alíquota do Simples) ou 3 (não informar)')
    if fiscal.aliquota_sn is not None and not Decimal('0') < fiscal.aliquota_sn < Decimal('100'):
        raise ProfileError('a alíquota do Simples Nacional deve ficar entre 0 e 100')
    if fiscal.aliquota_iss is not None and not Decimal('0') < fiscal.aliquota_iss < Decimal('10'):
        raise ProfileError('a alíquota do ISSQN deve ficar entre 0 e 10 (%)')
    if fiscal.aliquota_irrf is not None and not Decimal('0') < fiscal.aliquota_irrf <= Decimal('27.5'):
        raise ProfileError('a alíquota do IRRF deve ficar entre 0 e 27,5 (%)')
    for key in ('tributos_federais', 'tributos_estaduais', 'tributos_municipais'):
        value = getattr(fiscal, key)
        if value is not None and not Decimal('0') <= value < Decimal('1000'):
            raise ProfileError(f'percentual inválido em {key}: {value}')


def normalize_service_code(text):
    """'080201' ou '08.02.01' -> '08.02.01'."""
    digits = only_digits(text)
    if len(digits) != 6:
        raise ProfileError(f'código de tributação nacional deve ter 6 dígitos (recebido: {text!r})')
    return f'{digits[:2]}.{digits[2:4]}.{digits[4:]}'
