"""Leitura do HTML do portal (aplicação renderizada no servidor)."""
import re

from bs4 import BeautifulSoup

from .errors import NfseAuthError, PortalChangedError
from .models import CancelPreview, NotaDetalhe, NotaResumo, NotasPage, Rascunho
from .values import clean, parse_money

CHAVE_RE = re.compile(r'(?<!\d)(\d{50})(?!\d)')


def soup_of(html):
    return BeautifulSoup(html, 'lxml')


# ----------------------------------------------------------------------- login
def parse_login_token(html):
    field = soup_of(html).select_one('form input[name=__RequestVerificationToken]')
    if not field or not field.get('value'):
        raise NfseAuthError('token do formulário de login não encontrado')
    return field['value']


def parse_login_error(html):
    return '; '.join(parse_validation_errors(html))[:300]


def parse_identity(html):
    """Nome e inscrição do usuário logado, lidos do cabeçalho do portal."""
    soup = soup_of(html)
    nome = soup.select_one('li.perfil span.nome')
    inscricao = soup.select_one('li.perfil span.inscricao')
    digits = re.sub(r'\D', '', inscricao.get_text()) if inscricao is not None else ''
    if not digits:
        raise PortalChangedError('identificação do contribuinte logado não encontrada')
    return {'inscricao': digits, 'nome': clean(nome.get_text()) if nome is not None else ''}


# ------------------------------------------------------------------ formulários
def parse_validation_errors(html):
    """Mensagens de erro exibidas pelo portal em uma página devolvida com HTTP 200."""
    soup = soup_of(html)
    errors = []
    selectors = (
        '.field-validation-error',
        '.validation-summary-errors li',
        '.alert-danger',
        '.alert-warning',
        '.alert.erro',
        '#erroAssinatura',
    )
    for selector in selectors:
        for element in soup.select(selector):
            text = clean(element.get_text(' '))
            if text and text not in errors:
                errors.append(text)
    return errors


def form_field_names(html, selector='form.formdps'):
    """Nomes dos campos do formulário de um passo da emissão, na ordem do HTML."""
    soup = soup_of(html)
    form = soup.select_one(selector) or soup.find('form')
    if form is None:
        raise PortalChangedError('formulário do passo de emissão não encontrado na página')
    names = []
    for element in form.find_all(['input', 'select', 'textarea']):
        name = element.get('name')
        if name and name not in names:
            names.append(name)
    return names


def hidden_value(html, element_id):
    element = soup_of(html).find(id=element_id)
    return element.get('value', '') if element is not None else None


def select_options(html, element_id):
    element = soup_of(html).find('select', id=element_id)
    if element is None:
        return {}
    return {o.get('value', ''): clean(o.get_text()) for o in element.find_all('option') if o.get('value')}


# ------------------------------------------------------------------- listagens
def _total_and_last_page(soup):
    text = clean(soup.get_text(' '))
    match = re.search(r'Total de (\d+) registros?', text)
    total = int(match.group(1)) if match else None
    pages = [int(m) for m in re.findall(r'[?&]pg=(\d+)', ' '.join(a.get('href', '') for a in soup.find_all('a')))]
    return total, max(pages) if pages else 1


def parse_notas(html, pagina=1):
    """Tabela de NFS-e emitidas ou recebidas."""
    soup = soup_of(html)
    notas = []
    for row in soup.select('table tbody tr[data-chave]'):
        link = row.select_one('a[href*="/Notas/Visualizar/Index/"]')
        match = CHAVE_RE.search(link['href']) if link else None
        if not match:
            continue
        person = row.select_one('.td-texto-grande')
        doc_el = person.select_one('span.cpf, span.cnpj') if person else None
        documento = re.sub(r'\D', '', doc_el.get_text()) if doc_el else ''
        nome = clean(person.get_text(' ')) if person else ''
        if doc_el:
            nome = nome.split(' - ', 1)[1] if ' - ' in nome else nome
        situacao_img = row.select_one('.td-situacao img')
        situacao = ''
        if situacao_img is not None:
            situacao = situacao_img.get('data-original-title') or situacao_img.get('alt') or ''
        cells = row.find_all('td')
        notas.append(
            NotaResumo(
                chave_acesso=match.group(1),
                row_key=row['data-chave'],
                data_geracao=clean(cells[0].get_text()) if cells else '',
                tomador_documento=documento,
                tomador_nome=nome,
                competencia=clean(row.select_one('.td-competencia').get_text())
                if row.select_one('.td-competencia')
                else '',
                municipio=clean(row.select_one('.td-center').get_text()) if row.select_one('.td-center') else '',
                valor=parse_money(row.get('data-valor', '')),
                situacao_codigo=row.get('data-situacao', ''),
                situacao=clean(situacao),
                pode_cancelar=row.select_one('.btnCancelar') is not None,
                pode_substituir=row.select_one('.btnSubstituir') is not None,
            )
        )
    total, ultima = _total_and_last_page(soup)
    if total is None:
        total = len(notas)
    return NotasPage(notas=notas, total=total, pagina=pagina, ultima_pagina=max(ultima, pagina))


def parse_rascunhos(html):
    soup = soup_of(html)
    rascunhos = []
    for row in soup.select('table tbody tr[data-id]'):
        cells = row.find_all('td')
        tipo_img = cells[0].find('img') if cells else None
        passo = row.select_one('.td-situacao [title], .td-situacao [data-original-title]')
        editar = row.select_one('a[href*="idr="]')
        rascunhos.append(
            Rascunho(
                id=row['data-id'],
                tipo=(tipo_img.get('alt') or tipo_img.get('title') or '') if tipo_img else '',
                atualizado_em=clean(cells[1].get_text()) if len(cells) > 1 else '',
                competencia=clean(cells[2].get_text()) if len(cells) > 2 else '',
                emitida_para=clean(cells[3].get_text(' ')) if len(cells) > 3 else '',
                valor=parse_money(cells[4].get_text()) if len(cells) > 4 else None,
                passo=(passo.get('title') or passo.get('data-original-title') or '') if passo else '',
                editar_url=editar['href'] if editar else '',
            )
        )
    return rascunhos


# --------------------------------------------------------------------- detalhes
def parse_nota_detalhe(html):
    """Página 'Visualizar' de uma NFS-e: seções com pares rótulo/valor."""
    soup = soup_of(html)
    secoes = {}
    # Os eventos (ex.: cancelamento) vêm em painéis `pnlSujeito`, fora do padrão `panel`.
    for panel in soup.select('div.panel, div.pnlSujeito'):
        title = panel.select_one('h3.panel-title')
        if title is None:
            continue
        fields = {}
        for group in panel.select('div.form-group'):
            label = group.find('label')
            value = group.select_one('.form-control-static')
            if label is None or value is None:
                continue
            fields[clean(label.get_text(' '))] = clean(value.get_text(' '))
        if fields:
            secoes[clean(title.get_text(' '))] = fields
    chave = (secoes.get('Identificação da NFS-e') or {}).get('Chave de acesso', '')
    if not CHAVE_RE.fullmatch(chave):
        raise PortalChangedError('página da NFS-e sem chave de acesso reconhecível')
    row_key = ''
    print_link = soup.select_one('a[href*="/Notas/Visualizar/Impressao?chave="]')
    if print_link:
        row_key = print_link['href'].split('chave=', 1)[1]
    return NotaDetalhe(chave_acesso=chave, row_key=row_key, secoes=secoes)


def parse_conferencia(html):
    """Passo final 'Emitir NFS-e': resumo da DPS antes da emissão."""
    soup = soup_of(html)
    secoes = {}
    for title in soup.select('h4.emissao-titulo'):
        content = title.find_next_sibling('div', class_='emissao-conteudo')
        if content is None:
            continue
        fields = {}
        for term in content.find_all('dt'):
            definition = term.find_next_sibling('dd')
            if definition is not None:
                fields[clean(term.get_text(' ')).rstrip(':').strip()] = clean(definition.get_text(' '))
        for item in content.select('ul.leaders li'):
            spans = item.find_all('span', recursive=False)
            if len(spans) >= 2:
                key = clean(spans[0].get_text(' ')).replace(' :', '').rstrip(':').strip()
                fields[key] = clean(spans[-1].get_text(' '))
        name = clean(title.get_text(' '))
        secoes.setdefault(name, {}).update(fields)
    button = soup.find(id='btnProsseguir')
    return {
        'secoes': secoes,
        'modo_emissao': button.get('value', '') if button is not None else None,
        'draft_id': hidden_value(html, 'hdfIdr'),
        'representado': hidden_value(html, 'hdfUsrRepInsc') or '',
    }


def parse_emissao_resultado(html):
    """Página devolvida pela emissão: 'A NFS-e foi gerada com sucesso' e a chave de acesso."""
    soup = soup_of(html)
    alerta = soup.select_one('.alert-success')
    mensagem = clean(alerta.get_text(' ')) if alerta is not None else ''
    chave = ''
    for term in soup.find_all('dt'):
        if clean(term.get_text(' ')).rstrip(':').strip().lower() == 'chave de acesso':
            definition = term.find_next_sibling('dd')
            match = CHAVE_RE.search(clean(definition.get_text(' '))) if definition is not None else None
            chave = match.group(1) if match else ''
            break
    return {'sucesso': bool(chave) and 'sucesso' in mensagem.lower(), 'chave_acesso': chave, 'mensagem': mensagem}


# ----------------------------------------------------------------- cancelamento
def parse_cancel_modal(html, tipo_evento, mensagem=''):
    soup = soup_of(html)
    form = soup.find('form')
    if form is None:
        raise PortalChangedError('formulário de cancelamento não encontrado')
    fields = {}
    for element in form.find_all('input'):
        name = element.get('name')
        if name:
            fields[name] = element.get('value', '')
    motivos = {}
    select = form.find('select', attrs={'name': 'MotivoCancelamento'})
    if select is not None:
        motivos = {o['value']: clean(o.get_text()) for o in select.find_all('option') if o.get('value')}
    return CancelPreview(
        chave_acesso=fields.get('ChaveAcesso', ''),
        tipo_evento=tipo_evento,
        mensagem=mensagem or '',
        form_action=form.get('action', ''),
        fields=fields,
        motivos=motivos,
    )
