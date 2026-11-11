import json
import re
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from nfseai import Servico, Tributacao
from nfseai.transport import PortalResponse

FIXTURES = Path(__file__).parent / 'fixtures'
BASE = 'https://www.nfse.gov.br'
APP = '/EmissorNacional'
EMITTER = '11222333000181'
EMPTY_LIST = '<html><body><table><tbody></tbody></table></body></html>'
SERVICO = Servico(
    codigo_tributacao_nacional='08.02.01',
    codigo_nbs='122051900',
    descricao='Valor referente aos treinamentos prestados.',
)
TRIBUTACAO = Tributacao(tipo_valor_tributos=4, aliquota_sn=Decimal('3.00'))


def load(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


def response(text='', *, status=200, url=f'{BASE}{APP}', headers=None, redirects=None):
    return PortalResponse(
        status_code=status,
        url=url,
        headers=httpx.Headers(headers or {}),
        content=text.encode(),
        text=text,
        redirects=list(redirects or []),
    )


class FakeSession:
    """Substitui PortalSession: responde por rota e registra as chamadas."""

    inscricao = EMITTER

    def __init__(self):
        self.calls = []
        self.routes = {}
        self.alive_checks = 0

    def on(self, method, path, handler):
        self.routes[(method, path)] = handler

    def _dispatch(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        for (route_method, route_path), handler in self.routes.items():
            if route_method == method and (path == route_path or path.startswith(route_path + '/')):
                result = handler(path=path, **kwargs) if callable(handler) else handler
                return result if isinstance(result, PortalResponse) else response(result)
        raise AssertionError(f'rota não prevista no teste: {method} {path}')

    def get(self, path, params=None, **kwargs):
        return self._dispatch('GET', path, params=params, **kwargs)

    def post(self, path, params=None, data=None, **kwargs):
        return self._dispatch('POST', path, params=params, data=data, **kwargs)

    def get_json(self, path, params=None):
        return json.loads(self._dispatch('GET', path, params=params).text)

    def check_alive(self):
        self.alive_checks += 1

    def count(self, method, path):
        return sum(1 for m, p, _ in self.calls if m == method and p == path)

    def last_body(self, path):
        for method, called, kwargs in reversed(self.calls):
            if method == 'POST' and called == path:
                return kwargs.get('data')
        raise AssertionError(f'nenhum POST em {path}')


@pytest.fixture
def captured():
    return json.loads(load('captured_posts.json'))


@pytest.fixture
def draft_id():
    return re.search(r'id="hdfIdr"[^>]*value="([^"]+)"', load('conferencia.html')).group(1)


@pytest.fixture
def session(draft_id):
    """Sessão falsa com o cenário mapeado: emitente ME/EPP, tomador PJ, serviço 08.02.01."""
    fake = FakeSession()
    api = f'{APP}/api/emissaodps'
    municipio = [{'Codigo': '4199999', 'Nome': 'Cidade Exemplo/PR', 'Grupo': None, 'Ordem': 0}]
    estabelecimento = {
        'InfCad': {
            'Inscricao': EMITTER,
            'InscricaoMunicipal': None,
            'RazaoSocial': 'EMPRESA EXEMPLO LTDA',
            'InfoEndereco': {'CEP': '85000-000', 'Logradouro': 'RUA DAS FLORES', 'Numero': '100', 'Bairro': 'CENTRO'},
            'Telefone': '(11) 30000000',
            'Email': 'CONTATO@EXEMPLO.COM.BR',
        }
    }
    tomador_pj = {
        'inscricao': '11444777000161',
        'nomerazaosocial': 'TOMADORA EXEMPLO LTDA',
        'cep': '85000000',
        'codigoibgemunicipio': 4199999,
        'nomemunicipio': 'Cidade Exemplo/PR',
        'bairro': 'CENTRO',
        'logradouro': 'AVENIDA CENTRAL',
        'numero': '200',
        'complemento': 'SALA 01',
    }
    servico = {
        'results': [{
            'id': '08.02.01',
            'text': '08.02.01 - Instrução, treinamento, orientação pedagógica e educacional, '
                    'avaliação de conhecimentos de qualquer natureza.',
        }]
    }
    incidencia = {
        'HaIncidenciaDeISSQN': True, 'TipoMunicipioIncidencia': 1, 'EhAtividadeObra': False,
        'EhAtividadeEvento': False, 'EhExportacao': False, 'Codigo': 4199999,
        'Nome': 'Cidade Exemplo/PR', 'Conveniado': True, 'EhConvenioVigente': True,
        'ListaTributacaoMunicipal': [], 'Mensagem': None,
    }
    fake.on('GET', f'{api}/ListarMunicipiosParaEmissao', json.dumps(municipio))
    fake.on('GET', f'{api}/recuperaropcaosn', json.dumps({'Tipo': 3, 'Descricao': 'Optante - ME/EPP'}))
    fake.on('GET', f'{api}/RecuperarInfoEstabelecimento', json.dumps(estabelecimento))
    fake.on('GET', f'{APP}/api/EmissaoDPS/RecuperarInfoPessoaJuridicaTomador', json.dumps(tomador_pj))
    fake.on('GET', f'{APP}/api/EmissaoDPS/RecuperarInfoInscricao', json.dumps(
        {'inscricao': '52998224725', 'nomerazaosocial': 'TOMADOR EXEMPLO 1', 'codigopais': 0}
    ))
    fake.on('GET', f'{api}/BuscarNomeServico', json.dumps(servico))
    fake.on('GET', f'{api}/VerificarIncidencia', json.dumps(incidencia))
    fake.on('GET', f'{APP}/DPS/Pessoas', load('form-step1.html'))
    fake.on('GET', f'{APP}/DPS/Servico', load('form-step2.html'))
    fake.on('GET', f'{APP}/DPS/Tributacao', load('form-step3.html'))
    fake.on('GET', f'{APP}/DPS/EmitirNFSe', load('conferencia.html'))
    fake.on('GET', f'{APP}/Notas/Emitidas', EMPTY_LIST)
    fake.on('GET', f'{APP}/Notas/Rascunhos', EMPTY_LIST)
    fake.on('POST', f'{APP}/Rascunhos/Excluir', '{}')

    def redirect(target):
        return response(status=302, headers={'location': f'{APP}/DPS/{target}?idr={draft_id}'})

    fake.on('POST', f'{APP}/DPS/Pessoas', lambda **_: redirect('Servico'))
    fake.on('POST', f'{APP}/DPS/Servico', lambda **_: redirect('Tributacao'))
    fake.on('POST', f'{APP}/DPS/Tributacao', lambda **_: redirect('EmitirNFSe'))
    return fake


MODAL_PATH = f'{APP}/Notas/Emitidas/ModalCancelamento/'
CANCEL_PATH = f'{APP}/Notas/Emitidas/CancelarNFSe'
JUSTIFICATIVA = 'Reembolso solicitado pelo cliente dentro do prazo de garantia.'


@pytest.fixture
def chave():
    return re.search(r'\d{50}', load('nota.html')).group(0)


@pytest.fixture
def cancel_session(session, chave):
    """A nota aparece ativa até o POST de cancelamento; depois, com o evento de cancelamento."""
    cancelled = load('nota-cancelada.html')
    cancelled = cancelled.replace(re.search(r'\d{50}', cancelled).group(0), chave)
    state = {'cancelled': False}

    def cancel(**_):
        state['cancelled'] = True
        return json.dumps({'TipoEvento': 101101, 'Processado': True, 'Sucesso': True, 'AssinarPedRegEvento': False})

    session.on('GET', f'{APP}/Notas/Visualizar/Index', lambda **_: cancelled if state['cancelled'] else load('nota.html'))
    session.on('POST', MODAL_PATH.rstrip('/'), json.dumps(
        {'Sucesso': True, 'TipoEvento': 101101, 'Mensagem': '', 'Html': load('modal-cancelamento.html')}
    ))
    session.on('POST', CANCEL_PATH, cancel)
    session.cancel_state = state
    return session
