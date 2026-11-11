"""PDF da nota a partir da versão para impressão do portal.

O portal protege o DANFSe oficial e o XML com hCaptcha, e esse bloqueio não é
contornado. O PDF gerado aqui é a versão para impressão que o próprio portal oferece
no botão "Imprimir" da nota, com todos os dados e a chave de acesso. O documento
fiscal continua sendo o XML.

A conversão usa o Chromium do Playwright, que é opcional: instale com
``nfseai instalar-pdf``.
"""
from pathlib import Path

from .config import APP_PATH, BASE_URL
from .errors import PdfUnavailableError

INSTALL_HINT = (
    'instale o suporte a PDF com `nfseai instalar-pdf` (Playwright + Chromium sem interface, cerca de 300 MB)'
)


def _strip(inner):
    """Faixa de cabeçalho/rodapé do PDF: identifica o documento sem alterar o conteúdo da nota."""
    style = 'font-size:8px;width:100%;padding:0 8mm;color:#555;display:flex;justify-content:space-between;'
    return f'<div style="{style}">{inner}</div>'


def pdf_support_available():
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def default_pdf_name(nota):
    return f'NFSe_{nota.numero_nfse}_{nota.chave_acesso}.pdf'


def render_nota_pdf(client, chave_acesso, dest_path):
    """Gera o PDF da versão para impressão da nota, usando a sessão já autenticada."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PdfUnavailableError(f'o Playwright não está instalado; {INSTALL_HINT}') from None

    nota = client.notas.get(chave_acesso)
    if not nota.row_key:
        raise PdfUnavailableError('link da versão para impressão não encontrado na nota')
    client.session.check_alive()
    cookies = [
        {'name': c['name'], 'value': c['value'], 'url': BASE_URL + '/'} for c in client.session.export_cookies()
    ]
    url = f'{BASE_URL}{APP_PATH}/Notas/Visualizar/Impressao?chave={nota.row_key}'
    dest_path = Path(dest_path).expanduser()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(locale='pt-BR', viewport={'width': 1240, 'height': 1754})
                context.add_cookies(cookies)
                page = context.new_page()
                response = page.goto(url, wait_until='networkidle')
                if response is None or response.status != 200 or '/Login' in page.url:
                    status = response.status if response else '?'
                    raise PdfUnavailableError(f'versão para impressão indisponível (HTTP {status})')
                if chave_acesso not in page.content():
                    raise PdfUnavailableError('a página de impressão não contém a chave de acesso solicitada')
                # Mídia de tela mantém o layout em colunas; a de impressão empilha tudo em seis páginas.
                page.emulate_media(media='screen')
                page.add_style_tag(content='#pnlComandos { display: none !important; }')
                page.pdf(
                    path=str(dest_path), format='A4', scale=0.62, print_background=True,
                    display_header_footer=True,
                    header_template=_strip(
                        f'<span>NFS-e nº {nota.numero_nfse} · Sistema Nacional NFS-e</span>'
                        '<span>Versão para impressão do Portal Nacional (www.nfse.gov.br)</span>'
                    ),
                    footer_template=_strip(
                        f'<span>Chave de acesso: {chave_acesso}</span>'
                        '<span><span class="pageNumber"></span>/<span class="totalPages"></span></span>'
                    ),
                    margin={'top': '14mm', 'bottom': '14mm', 'left': '8mm', 'right': '8mm'},
                )
            finally:
                browser.close()
    except PlaywrightError as exc:
        message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise PdfUnavailableError(f'o navegador não conseguiu gerar o PDF: {message}; {INSTALL_HINT}') from exc
    if not dest_path.is_file() or not dest_path.read_bytes().startswith(b'%PDF'):
        raise PdfUnavailableError('o arquivo gerado não é um PDF')
    dest_path.chmod(0o600)
    return dest_path
