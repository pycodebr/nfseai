#!/usr/bin/env python3
"""Gera `tests/fixtures` a partir das páginas reais salvas em `.state/`, sem dados reais.

Uso:
    python tools/build_fixtures.py [--state .state] [--out tests/fixtures] [--scrub tools/scrub.local.json]

Entrada (gerada com `tools/explore.py` e `tools/capture.py`):
    .state/pages/     login.html, home.html, emitidas.html, rascunhos.html, nota.html,
                      nota-cancelada.html, conferencia.html, emissao-sucesso.html,
                      form-step1.html, form-step2.html, form-step3.html, modal-cancelamento.html
    .state/captures/  step1.jsonl (tomador CNPJ), step1cpf.jsonl (tomador CPF), step2.jsonl, step3.jsonl

O arquivo de troca (`tools/scrub.local.json`, fora do Git; modelo em `tools/scrub.example.json`)
lista os textos reais da sua empresa e o valor fictício de cada um: CNPJ com e sem pontuação,
razão social, e-mail, telefone, endereço, município, CEP e o CNPJ/razão social do tomador usado
no mapeamento. Tomadores de listagens, tokens, identificadores de rascunho e chaves de acesso
(número da nota, código e dígito verificador) são trocados automaticamente. Depois de gerar, procure por dados reais nas fixtures antes de qualquer commit:

    grep -rn "<seu CNPJ>\\|<sua razão social>" tests/fixtures
"""
import argparse
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

FAKE_CPFS = ['52998224725', '11144477735', '39053344705']
FAKE_PJ = ('11.444.777/0001-61', 'TOMADORA EXEMPLO LTDA')
LONG_TOKEN = re.compile(r'(?<![A-Za-z0-9/])[A-Za-z0-9+_-]{60,}={0,2}')
IDR = re.compile(r'(idr=|id="hdfIdr"[^>]*value="|data-id="|data-idr=")([A-Za-z0-9]{20,})')
ACCESS_KEY = re.compile(r'(?<!\d)\d{50}(?!\d)')
DEFAULT_KEEP = ['1', '0', 'BR']


class Scrubber:
    def __init__(self, literals):
        self._literals = literals
        self._tokens = {}
        self._drafts = {}
        self._keys = {}

    def _token(self, match):
        value = match.group(0)
        if value.isdigit():
            return value
        return self._tokens.setdefault(value, f'ROWKEY{len(self._tokens) + 1:04d}')

    def _draft(self, match):
        return match.group(1) + self._drafts.setdefault(match.group(2), f'DRAFT{len(self._drafts) + 1:04d}')

    def _key(self, match):
        """Troca o número da nota, o código aleatório e o dígito verificador da chave de acesso.

        Com o CNPJ e o município verdadeiros (que são públicos), a chave original poderia ser
        remontada e usada na consulta pública da nota de um cliente.
        """
        key = match.group(0)
        index = len(self._keys) + 1
        return self._keys.setdefault(key, f'{key[:23]}{index:013d}{key[36:40]}{index:09d}0')

    def text(self, html):
        for old, new in self._literals:
            html = html.replace(old, new)
        html = IDR.sub(self._draft, html)
        html = ACCESS_KEY.sub(self._key, html)
        return LONG_TOKEN.sub(self._token, html)


def soup_of(path):
    soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'lxml')
    for element in soup(['script', 'style', 'footer', 'link', 'meta']):
        element.decompose()
    return soup


def fake_people(soup):
    """Troca documento e nome de pessoas em listagens."""
    index = 0
    for cell in soup.select('.td-texto-grande'):
        doc = cell.select_one('span.cpf, span.cnpj')
        if doc is None:
            continue
        number = index % len(FAKE_CPFS)
        container = doc.parent
        container.clear()
        if container.find('img') is None and cell.find_parent('tr').get('data-chave'):
            image = '<img src="/EmissorNacional/img/tb-tomador.svg" title="Tomador"/>'
            container.append(BeautifulSoup(image, 'lxml').img)
        span = soup.new_tag('span', attrs={'class': 'cpf'})
        span.string = FAKE_CPFS[number]
        container.append(span)
        container.append(f' - TOMADOR EXEMPLO {number + 1}')
        index += 1


def keep_rows(soup, limit=3):
    body = soup.select_one('table tbody')
    if body is None:
        return
    for row in body.find_all('tr')[limit:]:
        row.decompose()


def trim_selects(form, keep):
    for select in form.find_all('select'):
        for position, option in enumerate(select.find_all('option')):
            if not (position < 4 or option.has_attr('selected') or option.get('value') in keep):
                option.decompose()


def build(state, out, config):
    pages, captures = state / 'pages', state / 'captures'
    out.mkdir(parents=True, exist_ok=True)
    scrub = Scrubber([tuple(pair) for pair in config.get('literals', [])])
    keep = set(DEFAULT_KEEP) | set(config.get('keep_select_values', []))

    def save(name, html):
        (out / name).write_text(scrub.text(html), encoding='utf-8')

    # Login
    save('login.html', str(soup_of(pages / 'login.html').select_one('form')))

    # Home (identificação)
    soup = soup_of(pages / 'home.html')
    save('home.html', f'<html><body><ul>{soup.select_one("li.perfil")}</ul></body></html>')

    # Emitidas
    soup = soup_of(pages / 'emitidas.html')
    keep_rows(soup)
    fake_people(soup)
    main = soup.select_one('table').find_parent('div')
    pagination = soup.select_one('.paginacao') or soup.select_one('.pagination')
    total = re.search(r'Total de \d+ registros', soup.get_text(' '))
    save(
        'emitidas.html',
        f'<html><body>{main}{pagination or ""}<div class="descricao">{total.group(0)}</div></body></html>',
    )

    # Rascunhos
    soup = soup_of(pages / 'rascunhos.html')
    for cell in soup.select('.td-texto-grande'):
        if cell.select_one('span.cpf, span.cnpj'):
            cell.clear()
            fake = f'<div><span class="cnpj">{FAKE_PJ[0]}</span> - {FAKE_PJ[1]}</div>'
            cell.append(BeautifulSoup(fake, 'lxml').div)
    save('rascunhos.html', f'<html><body>{soup.select_one("table")}</body></html>')

    # Detalhe da nota (ativa e cancelada)
    for name in ('nota.html', 'nota-cancelada.html'):
        soup = soup_of(pages / name)
        for panel in soup.select('div.panel'):
            title = panel.select_one('h3.panel-title')
            if title is not None and title.get_text(strip=True) == 'Tomador':
                for group in panel.select('div.form-group'):
                    label = group.find('label').get_text(strip=True)
                    value = group.select_one('.form-control-static')
                    if label == 'CPF':
                        value.string = '529.982.247-25'
                    elif label == 'Nome/Razão Social':
                        value.string = 'TOMADOR EXEMPLO 1'
        panels = ''.join(str(p) for p in soup.select('div.panel, div.pnlSujeito'))
        links = ''.join(str(a) for a in soup.select('a[href*="/Notas/Visualizar/Impressao"]'))
        save(name, f'<html><body>{links}{panels}</body></html>')

    # Conferência (passo final)
    soup = soup_of(pages / 'conferencia.html')
    hidden = ''.join(str(i) for i in soup.find_all('input', type='hidden'))
    body = ''.join(str(d) for d in soup.select('div.pnlCollapse'))
    save('conferencia.html', f'<html><body>{hidden}{body}{soup.find(id="btnProsseguir")}</body></html>')

    # Página de sucesso da emissão
    soup = soup_of(pages / 'emissao-sucesso.html')
    main = soup.select_one('#main-content')
    for term in main.find_all('dt'):
        if term.get_text(strip=True).startswith('Tomador'):
            term.find_next_sibling('dd').string = '529.982.247-25 - TOMADOR EXEMPLO 1'
    save('emissao-sucesso.html', f'<html><body>{main}</body></html>')

    # Formulários dos três passos
    for step in ('step1', 'step2', 'step3'):
        soup = soup_of(pages / f'form-{step}.html')
        form = soup.select_one('form.formdps')
        trim_selects(form, keep)
        extra = ''.join(
            str(i) for i in soup.find_all('input', type='hidden') if not i.get('name') and i.find_parent('form') is None
        )
        save(f'form-{step}.html', f'<html><body>{extra}{form}</body></html>')

    # Modal de cancelamento
    soup = BeautifulSoup((pages / 'modal-cancelamento.html').read_text(encoding='utf-8'), 'lxml')
    for field in soup.find_all('input', attrs={'name': 'EmitidoPara'}):
        field['value'] = '52998224725 - TOMADOR EXEMPLO 1'
    for element in soup.find_all(string=re.compile(r'\d{11} - ')):
        element.replace_with('52998224725 - TOMADOR EXEMPLO 1')
    save('modal-cancelamento.html', str(soup.find('form')))

    # Corpos dos POSTs capturados no navegador (contrato dos três passos)
    captured = {}
    sources = (('step1', 'pessoas_pj'), ('step1cpf', 'pessoas_pf'), ('step2', 'servico'), ('step3', 'tributacao'))
    for name, key in sources:
        for line in (captures / f'{name}.jsonl').read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            if record['method'] == 'POST' and record['type'] == 'document':
                body = record['body_form']
                if key == 'pessoas_pf':
                    fake = {'Tomador.Inscricao': '529.982.247-25', 'Tomador.Nome': 'TOMADOR EXEMPLO 1'}
                    body = [[k, fake.get(k, v)] for k, v in body]
                captured[key] = json.loads(scrub.text(json.dumps(body, ensure_ascii=False)))
    (out / 'captured_posts.json').write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding='utf-8')
    for path in sorted(out.iterdir()):
        print(f'{path.name}: {path.stat().st_size} bytes')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--state', default='.state')
    parser.add_argument('--out', default='tests/fixtures')
    parser.add_argument('--scrub', default='tools/scrub.local.json')
    args = parser.parse_args(argv)
    scrub_path = Path(args.scrub)
    if not scrub_path.is_file():
        print(f'crie {scrub_path} a partir de tools/scrub.example.json com os dados reais a trocar', file=sys.stderr)
        return 1
    build(Path(args.state), Path(args.out), json.loads(scrub_path.read_text(encoding='utf-8')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
