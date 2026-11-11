import json
from decimal import Decimal

import pytest

from nfseai import FiscalProfileIncomplete, NotConnectedError, ProfileError, Store
from nfseai.profiles import DEFAULT_PROFILE, normalize_service_code, validate_profile_name

PASSWORD = 'segredo-123'


@pytest.fixture
def store(tmp_path, monkeypatch):
    for key in ('NFSEAI_USER', 'NFSEAI_PASSWORD', 'NFSEAI_EMPRESA', 'NFSEAI_ENV_FILE', 'NFSEAI_HOME'):
        monkeypatch.delenv(key, raising=False)
    return Store(home=tmp_path / 'home')


def test_credentials_are_private_and_never_in_messages(store):
    store.save_credentials('minha-empresa', '11.222.333/0001-81', PASSWORD)
    assert store.home.stat().st_mode & 0o777 == 0o700
    assert store.credentials_path.stat().st_mode & 0o777 == 0o600
    assert store.config_path.stat().st_mode & 0o777 == 0o600
    credentials = store.load_credentials('minha-empresa')
    assert credentials.inscricao == '11222333000181'
    assert PASSWORD not in repr(credentials)
    assert PASSWORD not in store.config_path.read_text()
    assert store.credentials_origin('minha-empresa') == 'arquivo'
    assert store.login_user('minha-empresa') == '11222333000181'


def test_first_company_becomes_default_and_resolution_order(store, monkeypatch):
    assert store.resolve() == DEFAULT_PROFILE
    store.save_credentials('alfa', '11222333000181', PASSWORD)
    assert store.default_profile() == 'alfa'
    store.save_credentials('beta', '11444777000161', PASSWORD)
    assert store.default_profile() == 'alfa'
    assert store.resolve() == 'alfa'
    assert store.resolve('beta') == 'beta'
    monkeypatch.setenv('NFSEAI_EMPRESA', 'beta')
    assert store.resolve() == 'beta'
    monkeypatch.delenv('NFSEAI_EMPRESA')
    assert store.set_default('beta') == 'beta'
    assert store.resolve() == 'beta'
    with pytest.raises(ProfileError, match='desconhecida'):
        store.set_default('gama')


def test_missing_credentials_tell_the_agent_what_to_do(store):
    with pytest.raises(NotConnectedError, match='nfseai conectar') as error:
        store.load_credentials('principal')
    assert 'nunca no chat' in str(error.value)
    assert store.credentials_origin('principal') is None
    assert store.login_user('principal') is None


def test_environment_overrides_file_and_supports_per_company_names(store, monkeypatch):
    store.save_credentials('minha-empresa', '11222333000181', PASSWORD)
    monkeypatch.setenv('NFSEAI_MINHA_EMPRESA_USER', '11.444.777/0001-61')
    monkeypatch.setenv('NFSEAI_MINHA_EMPRESA_PASSWORD', 'do-ambiente')
    credentials = store.load_credentials('minha-empresa')
    assert credentials.inscricao == '11444777000161'
    assert store.credentials_origin('minha-empresa') == 'ambiente'
    monkeypatch.delenv('NFSEAI_MINHA_EMPRESA_PASSWORD')
    with pytest.raises(NotConnectedError, match='NFSEAI_MINHA_EMPRESA_PASSWORD'):
        store.load_credentials('minha-empresa')


def test_generic_environment_and_dotenv_file(tmp_path, monkeypatch):
    for key in ('NFSEAI_USER', 'NFSEAI_PASSWORD', 'NFSEAI_EMPRESA'):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / '.env'
    env_file.write_text(f'NFSEAI_USER=11222333000181\nNFSEAI_PASSWORD={PASSWORD}\n')
    store = Store(home=tmp_path / 'home', env_file=str(env_file))
    assert store.load_credentials('principal').inscricao == '11222333000181'
    assert not store.credentials_path.exists()
    with pytest.raises(ProfileError, match='não encontrado'):
        Store(home=tmp_path / 'home', env_file=str(tmp_path / 'ausente.env')).load_credentials('principal')


def test_remove_credentials_keeps_fiscal_defaults(store):
    store.save_credentials('alfa', '11222333000181', PASSWORD)
    store.save_fiscal('alfa', codigo_tributacao_nacional='08.02.01', codigo_nbs='122051900', descricao='Treinamento.',
                      aliquota_sn=Decimal('3.00'))
    session = store.session_file('alfa')
    session.parent.mkdir(parents=True)
    session.write_text('[]')
    assert store.remove_credentials('alfa') is True
    assert not session.exists()
    assert PASSWORD not in store.credentials_path.read_text()
    assert store.fiscal('alfa').codigo_nbs == '122051900'
    assert store.remove_credentials('alfa') is False


def test_fiscal_defaults_roundtrip_and_partial_update(store):
    assert store.fiscal('alfa').missing() == ['codigo_tributacao_nacional', 'codigo_nbs', 'descricao', 'aliquota_sn']
    store.save_fiscal('alfa', codigo_tributacao_nacional='08.02.01', codigo_nbs='122051900')
    with pytest.raises(FiscalProfileIncomplete) as error:
        store.fiscal('alfa').servico()
    assert error.value.missing == ['descricao', 'aliquota_sn']
    store.save_fiscal('alfa', descricao='Treinamento prestado.', aliquota_sn=Decimal('6.00'))
    fiscal = store.fiscal('alfa')
    assert fiscal.missing() == []
    assert fiscal.servico().codigo_tributacao_nacional == '08.02.01'
    assert fiscal.tributacao().aliquota_sn == Decimal('6.00')
    saved = json.loads(store.config_path.read_text())
    assert saved['empresas']['alfa']['tributacao'] == {'tipo_valor_tributos': 4, 'aliquota_sn': '6.00'}
    store.save_fiscal('alfa', tipo_valor_tributos=3)
    assert store.fiscal('alfa').tributacao().tipo_valor_tributos == 3


def test_per_note_override_does_not_change_the_saved_defaults(store):
    store.save_fiscal('alfa', codigo_tributacao_nacional='08.02.01', codigo_nbs='122051900', descricao='Padrão.',
                      aliquota_sn=Decimal('3.00'))
    merged = store.fiscal('alfa').merged(descricao='Só desta nota.', codigo_nbs=None)
    assert merged.descricao == 'Só desta nota.'
    assert merged.codigo_nbs == '122051900'
    assert store.fiscal('alfa').descricao == 'Padrão.'


@pytest.mark.parametrize(('fields', 'match'), [
    ({'codigo_tributacao_nacional': '080201'}, '00.00.00'),
    ({'codigo_nbs': '1.2205.19.00'}, '9 dígitos'),
    ({'aliquota_sn': Decimal('100')}, 'entre 0 e 100'),
    ({'tipo_valor_tributos': 1}, 'valor aproximado'),
    ({'codigo_municipio_prestacao': '123'}, 'IBGE'),
    ({'descricao': 'x' * 2001}, '2000'),
    ({'campo_inexistente': 'x'}, 'desconhecidos'),
])
def test_invalid_fiscal_values_are_refused(store, fields, match):
    with pytest.raises(ProfileError, match=match):
        store.save_fiscal('alfa', **fields)
    assert not store.config_path.exists()


def test_profile_and_service_code_normalization():
    assert validate_profile_name(' Minha-Empresa ') == 'minha-empresa'
    for bad in ('', 'com espaço', '-x', 'ç', 'a' * 33, '../x'):
        with pytest.raises(ProfileError):
            validate_profile_name(bad)
    assert normalize_service_code('080201') == '08.02.01'
    assert normalize_service_code('08.02.01') == '08.02.01'
    with pytest.raises(ProfileError):
        normalize_service_code('0802')


def test_corrupted_config_is_reported(store):
    store.ensure_home()
    store.config_path.write_text('{não é json')
    with pytest.raises(ProfileError, match='corrompido'):
        store.profiles()


def test_several_companies_without_default_require_a_choice(store):
    store.save_credentials('alfa', '11222333000181', PASSWORD)
    store.save_credentials('beta', '11444777000161', PASSWORD)
    config = store.load_config()
    config['padrao'] = None
    store.save_config(config)
    with pytest.raises(ProfileError, match='mais de uma empresa'):
        store.resolve()
