#!/usr/bin/env python3
"""Exploração autenticada e somente leitura do Emissor Nacional, para remapear o portal.

Uso:
    python tools/explore.py [--empresa X] login
    python tools/explore.py [--empresa X] get <caminho> [--save NOME] [--links] [--noforms] [--scripts]
                                                         [--xhr] [--raw N]

Exemplos:
    python tools/explore.py get /EmissorNacional --save home
    python tools/explore.py get /EmissorNacional/Notas/Emitidas --save emitidas --links
    python tools/explore.py get /EmissorNacional/DPS/Pessoas --save form-step1

As credenciais são as da empresa conectada com `nfseai conectar` e nunca são impressas.
As páginas ficam em `.state/pages/` (fora do Git): elas contêm dados fiscais reais.
Só faz GET. Para capturar os POSTs dos formulários, use `tools/capture.py`.
"""
import argparse
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from nfseai import EmissorNacionalClient
from nfseai.config import APP_PATH, BASE_URL

TOKEN_RE = re.compile(r'[A-Za-z0-9_\-]{60,}')


def slug(path):
    return re.sub(r'[^A-Za-z0-9]+', '_', path).strip('_')[:120] or 'root'


def redact(text):
    return TOKEN_RE.sub('<TOKEN>', text)


def summarize(response, target, *, show_links=False, show_forms=True, show_scripts=False, raw=0):
    content_type = response.headers.get('content-type', '')
    print(f'== {response.url} -> {response.status_code} [{content_type}] {len(response.content)} bytes')
    if response.redirects:
        print('redirects:', ' -> '.join(response.redirects))
    if 'html' not in content_type:
        target.with_suffix('.bin').write_bytes(response.content)
        print('salvo em', target.with_suffix('.bin'))
    else:
        target.with_suffix('.html').write_text(response.text, encoding='utf-8')
        print('salvo em', target.with_suffix('.html'))
        soup = BeautifulSoup(response.text, 'lxml')
        print('title:', soup.title.get_text(strip=True) if soup.title else '')
        for alert in soup.select('.alert, .validation-summary-errors, .field-validation-error'):
            text = ' '.join(alert.get_text(' ', strip=True).split())
            if text:
                print('alert:', text[:300])
        if show_forms:
            for form in soup.find_all('form'):
                print(f"form: {form.get('method', 'get').upper()} {form.get('action')} id={form.get('id')}")
                for field in form.find_all(['input', 'select', 'textarea', 'button']):
                    name = field.get('name') or field.get('id')
                    if not name:
                        continue
                    value = field.get('value', '')
                    if field.name == 'select':
                        options = [
                            f"{o.get('value')}={' '.join(o.get_text(strip=True).split())[:40]}"
                            + ('*' if o.has_attr('selected') else '')
                            for o in field.find_all('option')
                        ]
                        value = ' | '.join(options[:12]) + (f' (+{len(options) - 12})' if len(options) > 12 else '')
                    extra = ' checked' if field.has_attr('checked') else ''
                    print(f"   {field.name}[{field.get('type', '')}] {name} = {redact(str(value))[:200]}{extra}")
        if show_links:
            seen = set()
            for anchor in soup.find_all('a'):
                href = anchor.get('href') or ''
                text = ' '.join(anchor.get_text(' ', strip=True).split()) or anchor.get('title', '')
                if not href or href.startswith('#') or (href, text) in seen:
                    continue
                seen.add((href, text))
                print(f'   link: {href}  [{text[:60]}]')
        if show_scripts:
            for script in soup.find_all('script'):
                if script.get('src'):
                    print('   script:', script['src'])
    if raw:
        print('--- raw')
        print(redact(response.text[:raw]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--empresa')
    parser.add_argument('--state', default='.state', help='pasta de trabalho (padrão: ./.state)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('login')
    get = sub.add_parser('get')
    get.add_argument('path')
    get.add_argument('--save', help='nome do arquivo em .state/pages (sem extensão)')
    get.add_argument('--links', action='store_true')
    get.add_argument('--noforms', action='store_true')
    get.add_argument('--scripts', action='store_true')
    get.add_argument('--xhr', action='store_true')
    get.add_argument('--raw', type=int, default=0)
    args = parser.parse_args(argv)

    pages = Path(args.state) / 'pages'
    pages.mkdir(parents=True, exist_ok=True)
    Path(args.state).chmod(0o700)
    with EmissorNacionalClient.from_profile(args.empresa) as client:
        if args.command == 'login':
            client.session.login()
            identity = client.whoami()
            print(f'login ok: {identity["nome"]} ({identity["inscricao"]})')
            return 0
        path = args.path if args.path.startswith('/') else f'{APP_PATH}/{args.path}'
        response = client.session.get(path, xhr=args.xhr)
        name = args.save or slug(str(response.url).replace(BASE_URL, ''))
        summarize(
            response, pages / name, show_links=args.links, show_forms=not args.noforms,
            show_scripts=args.scripts, raw=args.raw,
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
