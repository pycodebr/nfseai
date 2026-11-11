#!/usr/bin/env python3
"""Navegador com captura sanitizada das requisições, para remapear os formulários do portal.

Uso:
    nfseai whoami                                             # garante uma sessão válida
    python tools/capture.py step1 /EmissorNacional/DPS/Pessoas
    python tools/capture.py step2 "/EmissorNacional/DPS/Servico?idr=<ID>" --empresa X

Abre o Chromium já logado (reaproveita os cookies de `~/.nfseai/sessions/<empresa>.json`;
a senha não é digitada no navegador). Você preenche o passo na tela, como faria à mão, e
clica em "Avançar". Cada requisição que não é arquivo estático é gravada em
`.state/captures/<nome>.jsonl`, com campos de senha, token e cookie mascarados. Volte ao
terminal e aperte Enter para fechar.

NUNCA clique em "Emitir NFS-e" durante o mapeamento: pare na tela de conferência
(/DPS/EmitirNFSe) e leia o JavaScript do botão para saber o que ele faz.

As capturas contêm dados fiscais reais: ficam em `.state/`, fora do Git. Requer
`pip install playwright` e `python -m playwright install chromium`. Em servidor sem tela,
use `--headless --seconds N` só para conferir que a sessão abre.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl

from nfseai import Store
from nfseai.config import BASE_URL, DEFAULT_USER_AGENT

SENSITIVE = re.compile(r'(senha|password|token|authorization|cookie|captcha)', re.I)
STATIC = re.compile(r'\.(css|png|svg|jpg|gif|woff2?|ttf|ico)(\?|$)|/Content/|ruxitagent|/rb_|hcaptcha', re.I)


def redact_form(body):
    return [
        [key, '<redacted>' if SENSITIVE.search(key) else value]
        for key, value in parse_qsl(body or '', keep_blank_values=True)
    ]


def record_of(response):
    request = response.request
    if STATIC.search(request.url) or request.resource_type in ('image', 'font', 'stylesheet'):
        return None
    if request.resource_type == 'script' and request.method == 'GET':
        return None
    content_type = response.headers.get('content-type', '')
    record = {
        'method': request.method,
        'url': request.url,
        'type': request.resource_type,
        'status': response.status,
        'content_type': content_type,
        'req_content_type': request.headers.get('content-type', ''),
        'xhr': request.headers.get('x-requested-with', ''),
        'location': response.headers.get('location', ''),
    }
    post = request.post_data
    if post:
        if 'json' in record['req_content_type']:
            record['body_json'] = post[:6000]
        else:
            record['body_form'] = redact_form(post)
    if 'json' in content_type or request.resource_type in ('xhr', 'fetch'):
        try:
            record['response'] = response.text()[:6000]
        except Exception as exc:  # noqa: BLE001
            record['response'] = f'<unavailable: {exc}>'
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name', help='nome da captura (ex.: step1)')
    parser.add_argument('path', help='caminho inicial no portal')
    parser.add_argument('--empresa')
    parser.add_argument('--state', default='.state')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--seconds', type=int, default=0, help='fecha sozinho depois de N segundos')
    parser.add_argument('--save-page', help='salva o HTML final em .state/pages/<nome>.html')
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('instale o Playwright: pip install playwright && python -m playwright install chromium', file=sys.stderr)
        return 1

    store = Store()
    empresa = store.resolve(args.empresa)
    session_file = store.session_file(empresa)
    if not session_file.is_file():
        print(f'sem sessão salva para "{empresa}": rode `nfseai whoami` antes', file=sys.stderr)
        return 1
    cookies = json.loads(session_file.read_text())
    state = Path(args.state)
    captures = state / 'captures'
    captures.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    out_path = captures / f'{args.name}.jsonl'
    count = 0
    with out_path.open('a', encoding='utf-8') as out, sync_playwright() as playwright:
        out_path.chmod(0o600)
        browser = playwright.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT, locale='pt-BR', viewport={'width': 1400, 'height': 1000}
        )
        context.add_cookies([{'name': c['name'], 'value': c['value'], 'url': BASE_URL + '/'} for c in cookies])
        page = context.new_page()

        def on_response(response):
            nonlocal count
            record = record_of(response)
            if record is not None:
                out.write(json.dumps(record, ensure_ascii=False) + '\n')
                out.flush()
                count += 1

        page.on('response', on_response)
        try:
            url = args.path if args.path.startswith('http') else BASE_URL + args.path
            page.goto(url, wait_until='networkidle')
            if '/login' in page.url.lower():
                print('a sessão expirou: rode `nfseai whoami` e tente de novo', file=sys.stderr)
                return 1
            print('página aberta:', page.url)
            if args.seconds:
                time.sleep(args.seconds)
            else:
                input('Preencha o passo no navegador (não clique em "Emitir NFS-e"). Enter aqui para fechar... ')
            if args.save_page:
                pages = state / 'pages'
                pages.mkdir(parents=True, exist_ok=True)
                (pages / f'{args.save_page}.html').write_text(page.content(), encoding='utf-8')
        finally:
            fresh = context.cookies(BASE_URL + '/')
            session_file.write_text(json.dumps([
                {'name': c['name'], 'value': c['value'], 'domain': c['domain'], 'path': c['path']} for c in fresh
            ]))
            session_file.chmod(0o600)
            browser.close()
    print(f'{count} requisições gravadas em {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
