import io
from decimal import Decimal

import pytest

from nfseai import NfseAuthError, NfseHttpError, Store
from nfseai.wizard import Console, WizardAborted, configure, connect

PASSWORD = 'senha-super-secreta'


class Script:
    """Respostas digitadas pelo usuário, na ordem das perguntas."""

    def __init__(self, answers, password=PASSWORD):
        self.answers = list(answers)
        self.password = password
        self.prompts = []
        self.out = io.StringIO()

    def console(self):
        def ask(prompt):
            self.prompts.append(prompt)
            if not self.answers:
                raise EOFError
            return self.answers.pop(0)

        def secret(prompt):
            self.prompts.append(prompt)
            return self.password

        return Console(input_fn=ask, getpass_fn=secret, out=self.out)

    @property
    def text(self):
        return self.out.getvalue()


class FakeConsultas:
    def __init__(self):
        self.searches = []

    def buscar_servico(self, termo):
        self.searches.append(termo)
        catalog = [
            {'id': '08.02.01', 'text': '08.02.01 - Instrução, treinamento, orientação pedagógica e educacional.'},
            {'id': '01.07.01', 'text': '01.07.01 - Suporte técnico em informática.'},
        ]
        return [item for item in catalog if termo.lower() in item['text'].lower()]

    def buscar_municipio(self, termo):
        return [{'id': '4199999', 'text': 'Cidade Exemplo/PR'}] if 'exemplo' in termo.lower() else []


class FakeClient:
    def __init__(self, failure=None):
        self.failure = failure
        self.closed = False
        self.consultas = FakeConsultas()
        self.session_file = None
        self.received = None

    def whoami(self):
        if self.failure:
            raise self.failure
        return {'inscricao': '11222333000181', 'nome': 'EMPRESA EXEMPLO LTDA'}

    def close(self):
        self.closed = True
        if self.session_file is not None:
            self.session_file.write_text('[]')


@pytest.fixture
def store(tmp_path, monkeypatch):
    for key in ('NFSEAI_USER', 'NFSEAI_PASSWORD', 'NFSEAI_EMPRESA', 'NFSEAI_ENV_FILE'):
        monkeypatch.delenv(key, raising=False)
    return Store(home=tmp_path / 'home')


def factory_for(client):
    def factory(user, password, session_file):
        client.received = (user, password)
        client.session_file = session_file
        return client

    return factory


def test_connect_tests_login_once_and_saves_private_credentials(store):
    script = Script(['minha-empresa', '11.222.333/0001-81'])
    client = FakeClient()
    summary = connect(store, script.console(), factory_for(client))
    assert summary == {
        'empresa': 'minha-empresa', 'login': '11.222.333/0001-81', 'contribuinte': 'EMPRESA EXEMPLO LTDA',
        'login_testado': True, 'padrao': True,
    }
    assert client.received == ('11222333000181', PASSWORD)
    assert client.closed
    assert store.load_credentials('minha-empresa').password == PASSWORD
    assert store.credentials_path.stat().st_mode & 0o777 == 0o600
    assert PASSWORD not in script.text
    assert 'Login confirmado: EMPRESA EXEMPLO LTDA (11.222.333/0001-81)' in script.text
    assert any('não aparece enquanto você digita' in prompt for prompt in script.prompts)


def test_connect_uses_default_nickname_and_retries_invalid_document(store):
    script = Script(['', '11.222.333/0001-80', '11222333000181'])
    summary = connect(store, script.console(), factory_for(FakeClient()))
    assert summary['empresa'] == 'principal'
    assert 'CPF/CNPJ inválido' in script.text


@pytest.mark.parametrize(('failure', 'expected'), [
    (NfseAuthError('login recusado pelo portal (HTTP 200) Usuário ou senha inválidos.'), 'recusou o login'),
    (NfseHttpError('falha de rede em GET /EmissorNacional'), 'não foi possível falar com o portal'),
])
def test_failed_login_saves_nothing(store, failure, expected):
    script = Script(['alfa', '11222333000181'])
    client = FakeClient(failure=failure)
    with pytest.raises(WizardAborted, match=expected) as error:
        connect(store, script.console(), factory_for(client))
    assert 'Nada foi gravado' in str(error.value)
    assert PASSWORD not in str(error.value)
    assert client.closed
    assert not store.credentials_path.exists()
    assert not store.session_file('alfa').exists()
    assert store.profiles() == []


def test_connect_without_test_and_with_given_company(store):
    script = Script(['11222333000181'])

    def factory(*_):
        raise AssertionError('o login não deveria ser testado')

    summary = connect(store, script.console(), factory, empresa='beta', test_login=False)
    assert summary['login_testado'] is False and summary['contribuinte'] is None
    assert store.credentials_origin('beta') == 'arquivo'


def test_connect_aborts_on_empty_password_or_interrupted_input(store):
    with pytest.raises(WizardAborted, match='senha vazia'):
        connect(store, Script(['alfa', '11222333000181'], password='').console(), factory_for(FakeClient()))
    with pytest.raises(WizardAborted, match='interrompido'):
        connect(store, Script(['alfa']).console(), factory_for(FakeClient()))
    with pytest.raises(WizardAborted, match='limite de tentativas'):
        connect(store, Script(['alfa', '1', '2', '3']).console(), factory_for(FakeClient()))
    assert not store.credentials_path.exists()


def test_connect_asks_before_replacing_saved_credentials(store):
    store.save_credentials('alfa', '11222333000181', 'antiga')
    with pytest.raises(WizardAborted, match='mantidas'):
        connect(store, Script(['n']).console(), factory_for(FakeClient()), empresa='alfa')
    assert store.load_credentials('alfa').password == 'antiga'
    connect(store, Script(['s', '11222333000181']).console(), factory_for(FakeClient()), empresa='alfa')
    assert store.load_credentials('alfa').password == PASSWORD


def test_configure_with_search_and_simples_rate(store):
    script = Script(['treinamento', '1', '1.2205.19.00', 'Valor referente aos treinamentos prestados.', '1', '6,00', ''])
    client = FakeClient()
    saved = configure(store, script.console(), 'alfa', client)
    assert client.consultas.searches == ['treinamento']
    assert saved.codigo_tributacao_nacional == '08.02.01'
    assert saved.codigo_nbs == '122051900'
    assert saved.aliquota_sn == Decimal('6.00') and saved.tipo_valor_tributos == 4
    assert saved.codigo_municipio_prestacao == ''
    assert store.fiscal('alfa').missing() == []
    assert 'nfseai preparar' in script.text


def test_configure_with_code_checked_on_portal_and_no_tax_estimate(store):
    script = Script(['99.99.99', '080201', '122051900', 'Serviço prestado.', '2', 'n', 'exemplo', '1'])
    saved = configure(store, script.console(), 'alfa', FakeClient())
    assert 'não reconhece esse código' in script.text
    assert saved.codigo_tributacao_nacional == '08.02.01'
    assert saved.tipo_valor_tributos == 3 and saved.aliquota_sn is None
    assert (saved.codigo_municipio_prestacao, saved.nome_municipio_prestacao) == ('4199999', 'Cidade Exemplo/PR')
    assert store.fiscal('alfa').missing() == []


def test_configure_offline_accepts_code_and_keeps_current_values_as_defaults(store):
    store.save_fiscal('alfa', codigo_tributacao_nacional='08.02.01', codigo_nbs='122051900', descricao='Antiga.',
                      aliquota_sn=Decimal('3.00'))
    script = Script(['palavra', '', '', 'Nova descrição.', '', '', ''])
    saved = configure(store, script.console(), 'alfa', client=None)
    assert 'informe o código' in script.text
    assert saved.codigo_tributacao_nacional == '08.02.01'
    assert saved.descricao == 'Nova descrição.'
    assert saved.aliquota_sn == Decimal('3.00')


def test_configure_refuses_invalid_values_and_saves_nothing(store):
    script = Script(['08.02.01', '123', '12', '1'])
    with pytest.raises(WizardAborted, match='NBS'):
        configure(store, script.console(), 'alfa', client=None)
    assert not store.config_path.exists()
