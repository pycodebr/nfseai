import httpx
import pytest

from conftest import APP, BASE, load
from nfseai import Credentials, NfseAuthError, NfseHttpError, PortalSession

PASSWORD = 'senha-super-secreta'


class Portal:
    """Simula o login por formulário e a expiração da sessão."""

    def __init__(self, accept_login=True):
        self.accept_login = accept_login
        self.logged = False
        self.requests = []
        self.expire_once = False

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == f'{APP}/Login':
            if request.method == 'GET':
                return httpx.Response(200, html=load('login.html'))
            if self.accept_login and PASSWORD in request.content.decode():
                self.logged = True
                return httpx.Response(302, headers={'location': APP})
            return httpx.Response(200, html='<div class="validation-summary-errors"><ul><li>Usuário ou senha inválidos.</li></ul></div>')
        if not self.logged or self.expire_once:
            self.expire_once = False
            self.logged = False
            return httpx.Response(302, headers={'location': f'{APP}/Login?ReturnUrl=%2fEmissorNacional'})
        if path == f'{APP}/redirect':
            return httpx.Response(302, headers={'location': f'{APP}/final'})
        if path == f'{APP}/erro':
            return httpx.Response(500, text='erro')
        return httpx.Response(200, text=f'{request.method} {path} {request.content.decode()}')

    def count(self, method, path):
        return sum(1 for r in self.requests if r.method == method and r.url.path == path)


def make_session(portal, tmp_path=None):
    client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(portal), follow_redirects=False)
    session_file = tmp_path / 'session.json' if tmp_path else None
    return PortalSession(
        Credentials(user='11222333000181', password=PASSWORD), http_client=client, min_interval=0,
        session_file=session_file,
    )


def test_login_and_request():
    portal = Portal()
    session = make_session(portal)
    response = session.get(f'{APP}/Notas/Emitidas')
    assert response.status_code == 200
    assert portal.count('POST', f'{APP}/Login') == 1
    login_post = next(r for r in portal.requests if r.method == 'POST')
    assert b'__RequestVerificationToken=' in login_post.content
    assert b'Inscricao=11222333000181' in login_post.content


def test_login_refused_does_not_leak_password():
    session = make_session(Portal(accept_login=False))
    with pytest.raises(NfseAuthError) as error:
        session.get(f'{APP}/Notas/Emitidas')
    assert 'inválidos' in str(error.value)
    assert PASSWORD not in str(error.value)


def test_expired_session_logs_in_again_once():
    portal = Portal()
    session = make_session(portal)
    session.get(APP)
    portal.expire_once = True
    response = session.get(f'{APP}/Notas/Emitidas')
    assert response.status_code == 200
    assert portal.count('POST', f'{APP}/Login') == 2
    assert portal.count('GET', f'{APP}/Notas/Emitidas') == 2


def test_expired_session_without_retry_raises():
    portal = Portal()
    session = make_session(portal)
    session.get(APP)
    portal.expire_once = True
    with pytest.raises(NfseAuthError, match='expirada'):
        session.get(f'{APP}/DPS/NFSe/Index', retry_auth=False)
    assert portal.count('GET', f'{APP}/DPS/NFSe/Index') == 1
    assert portal.count('POST', f'{APP}/Login') == 1


def test_redirects_are_followed_and_recorded():
    session = make_session(Portal())
    response = session.get(f'{APP}/redirect')
    assert response.path == f'{APP}/final'
    assert response.redirects == [f'{APP}/final']


def test_follow_false_returns_the_redirect():
    session = make_session(Portal())
    response = session.post(f'{APP}/redirect', data={'a': '1'}, follow=False)
    assert response.status_code == 302
    assert response.headers['location'] == f'{APP}/final'


def test_pair_list_body_keeps_order_and_duplicates():
    session = make_session(Portal())
    body = [('B.Campo', 'NAO'), ('A', 'São João/PR'), ('B.Campo', 'NAO'), ('Vazio', '')]
    response = session.post(f'{APP}/DPS/Tributacao', data=body)
    assert response.text.endswith('B.Campo=NAO&A=S%C3%A3o+Jo%C3%A3o%2FPR&B.Campo=NAO&Vazio=')


def test_http_error_is_raised():
    session = make_session(Portal())
    with pytest.raises(NfseHttpError) as error:
        session.get(f'{APP}/erro')
    assert error.value.status_code == 500


def test_session_file_is_private_and_reused(tmp_path):
    portal = Portal()
    session = make_session(portal, tmp_path)
    session.get(APP)
    session.close()
    path = tmp_path / 'session.json'
    assert path.stat().st_mode & 0o777 == 0o600
    assert PASSWORD not in path.read_text()
