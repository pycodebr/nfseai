import json
import re
from datetime import date
from decimal import Decimal

import pytest

from conftest import APP, BASE, load, response
from nfseai import EmissorNacionalClient, NfseValidationError, Store
from nfseai import cli as cli_module
from nfseai.cli import main, parse_date, parse_valor

PASSWORD = 'senha-super-secreta'
EMIT_PATH = f'{APP}/DPS/NFSe/Index'
FISCAL_FLAGS = [
    '--codigo-servico', '080201', '--nbs', '122051900', '--descricao', 'Valor referente aos treinamentos prestados.',
    '--aliquota-sn', '3,00',
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    for key in ('NFSEAI_USER', 'NFSEAI_PASSWORD', 'NFSEAI_EMPRESA', 'NFSEAI_ENV_FILE'):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / 'home'
    monkeypatch.setenv('NFSEAI_HOME', str(path))
    return path


@pytest.fixture
def connected(home):
    store = Store()
    store.save_credentials('minha-empresa', '11222333000181', PASSWORD)
    return store


@pytest.fixture
def portal(session, monkeypatch):
    """Troca a sessão real do portal pela sessão falsa dos testes."""
    nota_html = load('nota.html')
    session.key = re.search(r'\d{50}', nota_html).group(0)
    conferencia = load('conferencia.html').replace('11.444.777/0001-61', '529.982.247-25')
    session.on('GET', f'{APP}/DPS/EmitirNFSe', conferencia.replace('R$ 10,00', 'R$ 1.047,51'))
    session.on('GET', EMIT_PATH, lambda **_: response(
        '<html>NFS-e gerada</html>', url=f'{BASE}{APP}/DPS/NFSe/Concluida?chaveAcesso={session.key}'
    ))
    session.on('GET', f'{APP}/Notas/Visualizar/Index', nota_html)
    session.on('GET', APP, load('home.html'))
    session.close = lambda: None

    def from_profile(cls, empresa=None, **_):
        Store().load_credentials(Store().resolve(empresa))
        return cls(session)

    monkeypatch.setattr(EmissorNacionalClient, 'from_profile', classmethod(from_profile))
    return session


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    data = json.loads(captured.out) if captured.out.strip() else None
    return code, data, captured.err


def test_status_guides_the_agent_when_not_connected(home, capsys):
    code, data, _ = run(capsys, 'status')
    assert code == 3
    assert data['conectada'] is False and data['empresa'] == 'principal'
    assert 'nfseai conectar' in data['proximo_passo'] and 'nunca no chat' in data['proximo_passo']


def test_status_reports_missing_fiscal_defaults_then_ready(connected, capsys):
    code, data, _ = run(capsys, 'status')
    assert code == 4
    assert data['conectada'] is True and data['origem_credenciais'] == 'arquivo'
    assert data['login'] == '11.222.333/0001-81'
    assert data['perfil_fiscal']['faltando'] == ['codigo_tributacao_nacional', 'codigo_nbs', 'descricao', 'aliquota_sn']
    assert 'nfseai configurar' in data['proximo_passo']

    code, data, _ = run(capsys, 'configurar', *FISCAL_FLAGS)
    assert code == 0 and data['perfil_fiscal']['completo'] is True
    assert data['perfil_fiscal']['codigo_tributacao_nacional'] == '08.02.01'
    code, data, _ = run(capsys, 'status')
    assert code == 0 and data['perfil_fiscal']['completo'] is True


def test_status_online_reads_the_taxpayer_from_the_portal(connected, portal, capsys):
    run(capsys, 'configurar', *FISCAL_FLAGS)
    code, data, _ = run(capsys, 'status', '--online')
    assert code == 0
    assert data['portal'] == {'ok': True, 'inscricao': '11222333000181', 'nome': 'EMPRESA EXEMPLO LTDA'}


def test_interactive_commands_refuse_to_run_without_a_terminal(home, capsys):
    for command in ('conectar', 'connect', 'configurar'):
        code, data, err = run(capsys, command)
        assert code == 2 and data is None
        assert 'terminal' in err and 'nunca no chat' in err
    assert Store().profiles() == []


def test_commands_that_need_the_portal_exit_3_when_not_connected(home, capsys):
    code, data, err = run(capsys, 'emitidas')
    assert code == 3 and data is None
    assert 'NotConnectedError' in err and 'nfseai conectar' in err


def test_emission_without_fiscal_data_exits_4_before_touching_the_portal(connected, portal, capsys):
    code, data, err = run(capsys, 'preparar', '--documento', '529.982.247-25', '--valor', '10,00')
    assert code == 4 and data is None
    assert '--codigo-servico' in err and '--nbs' in err
    assert portal.calls == []


def test_fiscal_data_can_be_given_per_note(connected, portal, capsys):
    code, data, _ = run(
        capsys, 'preparar', '--documento', '529.982.247-25', '--valor', '1.047,51', '--competencia', '2026-11-09',
        *FISCAL_FLAGS,
    )
    assert code == 0
    assert data['ensaio'] is True and data['emitida'] is False and data['rascunho_descartado'] is True
    assert data['valor'] == '1047.51' and data['competencia'] == '2026-11-09'
    assert Store().fiscal('minha-empresa').missing()


def test_emit_is_a_rehearsal_until_confirmed_and_never_repeats_a_reference(connected, portal, capsys):
    run(capsys, 'configurar', *FISCAL_FLAGS)
    args = ['emitir', '--documento', '52998224725', '--valor', '1047.51', '--competencia', '09/11/2026',
            '--referencia', 'venda-1']
    code, data, _ = run(capsys, *args)
    assert code == 0 and data['ensaio'] is True and data['emitida'] is False
    assert '--confirm' in data['proximo_passo']
    assert portal.count('GET', EMIT_PATH) == 0

    code, data, _ = run(capsys, *args, '--confirm')
    assert code == 0 and data['emitida'] is True
    assert data['chave_acesso'] == portal.key and data['referencia'] == 'venda-1'
    assert portal.count('GET', EMIT_PATH) == 1

    code, data, err = run(capsys, *args, '--confirm')
    assert code == 1 and data is None
    assert 'DuplicateReferenceError' in err and portal.key in err
    assert portal.count('GET', EMIT_PATH) == 1

    code, data, _ = run(capsys, 'registro', '--referencia', 'venda-1')
    assert data['status'] == 'emitida' and data['historico'][0]['evento'] == 'emitida'
    code, data, _ = run(capsys, 'registro')
    assert data['total'] == 1 and data['emissoes'][0]['referencia'] == 'venda-1'


def test_emit_with_pdf_reports_missing_support_without_losing_the_note(connected, portal, capsys, monkeypatch):
    run(capsys, 'configurar', *FISCAL_FLAGS)

    def no_pdf(*_):
        raise cli_module.NfseError('o Playwright não está instalado')

    monkeypatch.setattr(cli_module, 'render_nota_pdf', no_pdf)
    code, data, _ = run(capsys, 'emitir', '--documento', '52998224725', '--valor', '1047.51', '--competencia',
                        '09/11/2026', '--confirm', '--pdf')
    assert code == 0 and data['emitida'] is True
    assert 'Playwright' in data['pdf_erro']


def test_company_flag_works_before_and_after_the_command(home, capsys):
    Store().save_credentials('alfa', '11222333000181', PASSWORD)
    Store().save_credentials('beta', '11444777000161', PASSWORD)
    assert run(capsys, '--empresa', 'beta', 'status')[1]['login'] == '11.444.777/0001-61'
    assert run(capsys, 'status', '--empresa', 'beta')[1]['login'] == '11.444.777/0001-61'
    assert run(capsys, 'status')[1]['empresa'] == 'alfa'
    code, data, _ = run(capsys, 'usar', 'beta')
    assert data == {'empresa_padrao': 'beta'}
    code, data, _ = run(capsys, 'empresas')
    assert data['empresa_padrao'] == 'beta'
    assert [item['nome'] for item in data['empresas']] == ['alfa', 'beta']


def test_environment_credentials_work_without_any_file(home, portal, capsys, monkeypatch):
    monkeypatch.setenv('NFSEAI_USER', '11222333000181')
    monkeypatch.setenv('NFSEAI_PASSWORD', PASSWORD)
    code, data, _ = run(capsys, 'status')
    assert code == 4 and data['origem_credenciais'] == 'ambiente' and data['empresa'] == 'principal'
    code, data, _ = run(capsys, 'whoami')
    assert code == 0 and data['nome'] == 'EMPRESA EXEMPLO LTDA'
    assert not (home / 'credentials.json').exists()


def test_disconnect_and_cancel_require_explicit_confirmation(connected, portal, capsys):
    code, _, err = run(capsys, 'desconectar')
    assert code == 1 and '--confirm' in err
    assert Store().credentials_origin('minha-empresa') == 'arquivo'
    code, _, err = run(capsys, 'cancelar', portal.key, '--motivo', 'erro', '--justificativa', 'curta', '--confirm')
    assert code == 1 and 'justificativa' in err
    code, data, _ = run(capsys, 'desconectar', '--confirm')
    assert code == 0 and data == {'empresa': 'minha-empresa', 'removida': True}
    assert Store().credentials_origin('minha-empresa') is None


def test_invalid_configuration_is_refused(connected, capsys):
    code, data, err = run(capsys, 'configurar', '--nbs', '123')
    assert code == 1 and '9 dígitos' in err
    code, data, err = run(capsys, 'configurar', '--codigo-servico', '08')
    assert code == 1 and '6 dígitos' in err


def test_listing_requires_a_period_for_all_pages(connected, portal, capsys):
    code, _, err = run(capsys, 'emitidas', '--todas')
    assert code == 2 and '--inicio' in err
    code, data, _ = run(capsys, 'emitidas', '--inicio', '2026-10-10', '--fim', '09/11/2026')
    assert code == 0 and data['notas'] == []


@pytest.mark.parametrize(('text', 'expected'), [
    ('1047.51', Decimal('1047.51')),
    ('1047,51', Decimal('1047.51')),
    ('1.047,51', Decimal('1047.51')),
    ('R$ 197,00', Decimal('197.00')),
    ('197', Decimal('197.00')),
])
def test_parse_valor(text, expected):
    assert parse_valor(text) == expected


@pytest.mark.parametrize('text', ['1.047', '10,005', 'abc', '', '0', '-5'])
def test_parse_valor_refuses_ambiguous_or_invalid(text):
    with pytest.raises(NfseValidationError):
        parse_valor(text)


def test_parse_date_accepts_both_formats():
    assert parse_date('09/11/2026') == date(2026, 11, 9)
    assert parse_date('2026-11-09') == date(2026, 11, 9)
    with pytest.raises(NfseValidationError):
        parse_date('11/09')
