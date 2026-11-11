"""Sessão HTTP autenticada no portal do contribuinte (Emissor Nacional)."""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from .config import APP_PATH, BASE_URL, DEFAULT_USER_AGENT
from .errors import NfseAuthError, NfseHttpError
from .parsing import parse_login_error, parse_login_token

REDIRECT_STATUSES = (301, 302, 303, 307, 308)


@dataclass
class PortalResponse:
    """Resposta final de uma requisição, já com os redirecionamentos seguidos."""

    status_code: int
    url: str
    headers: httpx.Headers
    content: bytes
    text: str
    redirects: list = field(default_factory=list)

    def json(self):
        return json.loads(self.text)

    @property
    def path(self):
        return urlsplit(self.url).path


def _is_login_location(location):
    return '/login' in urlsplit(location).path.lower()


class PortalSession:
    """Mantém o login por CNPJ/senha, renova a sessão expirada e limita o ritmo.

    O portal é uma aplicação ASP.NET MVC renderizada no servidor: a autenticação
    fica em cookies e não existe token de API. A sessão expira com 20 minutos de
    inatividade.
    """

    def __init__(
        self,
        credentials,
        *,
        session_file=None,
        timeout=60.0,
        min_interval=0.35,
        user_agent=DEFAULT_USER_AGENT,
        http_client=None,
    ):
        self._credentials = credentials
        self._session_file = Path(session_file) if session_file else None
        self._min_interval = min_interval
        self._last_request_at = 0.0
        self._authenticated = False
        self._client = http_client or httpx.Client(
            base_url=BASE_URL,
            headers={'User-Agent': user_agent, 'Accept-Language': 'pt-BR,pt;q=0.9'},
            follow_redirects=False,
            timeout=timeout,
        )
        self._load_cookies()

    @property
    def inscricao(self):
        """CNPJ/CPF (só dígitos) usado no login."""
        return self._credentials.inscricao

    def close(self):
        self._save_cookies()
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # ------------------------------------------------------------------ cookies
    def _load_cookies(self):
        if not self._session_file or not self._session_file.is_file():
            return
        try:
            cookies = json.loads(self._session_file.read_text())
        except (OSError, ValueError):
            return
        for cookie in cookies:
            self._client.cookies.set(
                cookie['name'], cookie['value'], domain=cookie.get('domain', ''), path=cookie.get('path', '/')
            )

    def _save_cookies(self):
        if not self._session_file:
            return
        data = [
            {'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path}
            for c in self._client.cookies.jar
        ]
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        self._session_file.write_text(json.dumps(data))
        self._session_file.chmod(0o600)

    def export_cookies(self):
        """Cookies da sessão para abrir a mesma sessão em um navegador (ex.: gerar PDF)."""
        return [
            {'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path}
            for c in self._client.cookies.jar
        ]

    # -------------------------------------------------------------------- envio
    def _send(self, method, url, **kwargs):
        wait = self._min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise NfseHttpError(f'falha de rede em {method} {url}: {exc}', method=method, url=str(url)) from exc
        finally:
            self._last_request_at = time.monotonic()

    def login(self):
        """Autentica com CNPJ/senha. Faz uma única tentativa para não bloquear a conta."""
        self._client.cookies.clear()
        self._authenticated = False
        login_path = f'{APP_PATH}/Login'
        params = {'ReturnUrl': APP_PATH}
        page = self._send('GET', login_path, params=params)
        if page.status_code != 200:
            raise NfseAuthError(f'página de login indisponível (HTTP {page.status_code})')
        token = parse_login_token(page.text)
        response = self._send(
            'POST',
            login_path,
            params=params,
            data={
                '__RequestVerificationToken': token,
                'Inscricao': self._credentials.user,
                'Senha': self._credentials.password,
            },
            headers={'Referer': str(page.url), 'Origin': BASE_URL},
        )
        location = response.headers.get('location', '')
        if response.status_code not in REDIRECT_STATUSES or _is_login_location(location):
            detail = parse_login_error(response.text) if response.status_code == 200 else ''
            raise NfseAuthError(f'login recusado pelo portal (HTTP {response.status_code}) {detail}'.strip())
        self._authenticated = True
        self._save_cookies()

    def ensure_login(self):
        """Garante uma sessão válida antes de uma sequência de chamadas."""
        if self._authenticated:
            return
        if self._client.cookies.jar and self._probe():
            self._authenticated = True
            return
        self.login()

    def _probe(self):
        response = self._send('GET', APP_PATH)
        return response.status_code == 200

    def check_alive(self):
        """Confere a sessão no servidor e renova o login se ela expirou."""
        if not self._probe():
            self.login()

    def request(
        self,
        method,
        path,
        *,
        params=None,
        data=None,
        xhr=False,
        retry_auth=True,
        follow=True,
        max_redirects=6,
    ):
        """Executa uma requisição autenticada e segue redirecionamentos internos.

        Um redirecionamento para a tela de login significa sessão expirada: o portal
        não processou a requisição. Com `retry_auth=True` o cliente refaz o login e
        repete a chamada uma vez. A emissão usa `retry_auth=False`.
        """
        self.ensure_login()
        headers = {'X-Requested-With': 'XMLHttpRequest'} if xhr else {}
        body = {}
        if isinstance(data, list | tuple):
            # Lista de pares: preserva a ordem e os campos repetidos, como o navegador envia.
            body['content'] = urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        elif data is not None:
            body['data'] = data
        attempts = 0
        while True:
            attempts += 1
            response = self._send(method, path, params=params, headers=headers or None, **body)
            redirects = []
            expired = response.status_code == 401
            while follow and response.status_code in REDIRECT_STATUSES and not expired:
                location = response.headers.get('location', '')
                if _is_login_location(location):
                    expired = True
                    break
                if len(redirects) >= max_redirects:
                    raise NfseHttpError('redirecionamentos em excesso', method=method, url=str(response.url))
                redirects.append(location)
                response = self._send('GET', urljoin(str(response.url), location))
            if not follow and response.status_code in REDIRECT_STATUSES:
                expired = _is_login_location(response.headers.get('location', ''))
            if expired:
                self._authenticated = False
                if retry_auth and attempts == 1:
                    self.login()
                    continue
                raise NfseAuthError('sessão expirada no portal')
            break
        if response.status_code >= 400:
            raise NfseHttpError(
                f'{method} {path} retornou HTTP {response.status_code}',
                status_code=response.status_code,
                method=method,
                url=str(response.url),
            )
        return PortalResponse(
            status_code=response.status_code,
            url=str(response.url),
            headers=response.headers,
            content=response.content,
            text=response.text,
            redirects=redirects,
        )

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, **kwargs):
        return self.request('POST', path, **kwargs)

    def get_json(self, path, params=None):
        response = self.request('GET', path, params=params, xhr=True)
        try:
            return response.json()
        except ValueError as exc:
            raise NfseHttpError(
                f'resposta não-JSON em {path}', status_code=response.status_code, method='GET', url=response.url
            ) from exc
