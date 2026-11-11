from decimal import Decimal

from conftest import load
from nfseai.parsing import (
    form_field_names,
    parse_cancel_modal,
    parse_conferencia,
    parse_identity,
    parse_login_token,
    parse_nota_detalhe,
    parse_notas,
    parse_rascunhos,
    parse_validation_errors,
    select_options,
)


def test_login_token():
    assert parse_login_token(load('login.html'))


def test_identity():
    assert parse_identity(load('home.html')) == {'inscricao': '11222333000181', 'nome': 'EMPRESA EXEMPLO LTDA'}


def test_notas_listing():
    page = parse_notas(load('emitidas.html'))
    assert page.total == 216
    assert page.ultima_pagina == 15
    assert len(page.notas) == 3
    nota = page.notas[0]
    assert len(nota.chave_acesso) == 50
    assert nota.row_key.startswith('ROWKEY')
    assert nota.tomador_documento == '52998224725'
    assert nota.tomador_nome == 'TOMADOR EXEMPLO 1'
    assert nota.valor == Decimal('1047.51')
    assert nota.situacao_codigo == 'P100_GERADA'
    assert nota.situacao == 'NFS-e emitida'
    assert nota.competencia == '10/2026'
    assert nota.pode_cancelar and nota.pode_substituir


def test_nota_detalhe():
    nota = parse_nota_detalhe(load('nota.html'))
    assert len(nota.chave_acesso) == 50
    assert nota.row_key.startswith('ROWKEY')
    assert nota.situacao == '100 - NFS-e Gerada'
    assert not nota.cancelada
    assert nota.valor_servico == Decimal('1047.51')
    assert (nota.serie_dps, nota.numero_dps) == ('01000', '267')
    assert nota.tomador_documento == '52998224725'
    assert nota.campo('Serviço Prestado', 'Descrição do serviço') == 'Valor referente aos treinamentos prestados.'


def test_rascunhos():
    rascunhos = parse_rascunhos(load('rascunhos.html'))
    assert len(rascunhos) == 4
    assert all(r.id.startswith('DRAFT') and 'idr=' in r.editar_url for r in rascunhos)
    assert rascunhos[0].tipo == 'Emissão de NFS-e'
    assert rascunhos[0].passo == 'Emissão da NFS-e'
    assert rascunhos[0].valor == Decimal('10.00')
    assert {r.tipo for r in rascunhos} == {'Emissão de NFS-e', 'Substituição de NFS-e'}
    assert {r.passo for r in rascunhos} == {'Emissão da NFS-e', 'Serviço', 'Tributação'}


def test_conferencia():
    conferencia = parse_conferencia(load('conferencia.html'))
    secoes = conferencia['secoes']
    assert conferencia['modo_emissao'] == 'DPS'
    assert conferencia['representado'] == ''
    assert conferencia['draft_id'].startswith('DRAFT')
    assert secoes['Informações do Emitente']['CNPJ'] == '11.222.333/0001-81'
    assert secoes['Tomador/Adquirente do Serviço']['CNPJ'] == '11.444.777/0001-61'
    assert secoes['Valores do Serviço Prestado']['Valor da operação/serviço prestado'] == 'R$ 10,00'
    assert secoes['Informações Gerais']['Data de Competência'] == '09/11/2026'
    assert secoes['ISSQN calculado']['ISSQN'] == '-'
    assert secoes['Valor líquido da NFS-e']['Valor líquido da NFS-e'] == 'R$ 10,00'


def test_cancel_modal():
    preview = parse_cancel_modal(load('modal-cancelamento.html'), 101101)
    assert preview.cancelamento_direto
    assert preview.form_action == '/EmissorNacional/Notas/Emitidas/CancelarNFSe'
    assert preview.motivos == {'1': 'Erro na emissão', '2': 'Serviço não prestado', '9': 'Outros'}
    assert len(preview.chave_acesso) == 50
    assert preview.fields['TipoEvento'] == 'CANCELAMENTO'
    assert '__RequestVerificationToken' in preview.fields
    assert 'ROWKEY' not in repr(preview)


def test_form_fields_and_options():
    names = form_field_names(load('form-step3.html'))
    assert 'Valores.ValorServico' in names
    assert 'ValorTributos.AliquotaSN' in names
    assert '122051900' in select_options(load('form-step2.html'), 'ServicoPrestado_CodigoNBS')


def test_validation_errors():
    html = (
        '<div class="validation-summary-errors"><ul><li>Informe o tomador.</li></ul></div>'
        '<span class="field-validation-error">CPF inválido</span>'
    )
    assert parse_validation_errors(html) == ['CPF inválido', 'Informe o tomador.']
