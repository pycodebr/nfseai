from decimal import Decimal

import pytest

from nfseai.config import Credentials, format_cep, format_document, validate_document
from nfseai.errors import NfseValidationError
from nfseai.values import format_money, parse_money


@pytest.mark.parametrize(('text', 'expected'), [
    ('R$ 1.047,51', Decimal('1047.51')),
    ('10,00', Decimal('10.00')),
    ('3,00 %', Decimal('3.00')),
    ('-', None),
    ('', None),
    ('Não informado', None),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize(('value', 'expected'), [
    (Decimal('10'), '10,00'),
    (Decimal('1047.51'), '1.047,51'),
    (Decimal('1234567.8'), '1.234.567,80'),
    (Decimal('0.5'), '0,50'),
])
def test_format_money_follows_portal_mask(value, expected):
    assert format_money(value) == expected


def test_validate_document_accepts_valid_cpf_and_cnpj():
    assert validate_document('529.982.247-25') == '52998224725'
    assert validate_document('11.222.333/0001-81') == '11222333000181'


@pytest.mark.parametrize('document', ['529.982.247-26', '11.222.333/0001-80', '11111111111', '123', ''])
def test_validate_document_rejects_invalid(document):
    with pytest.raises(NfseValidationError):
        validate_document(document)


def test_format_document_and_cep():
    assert format_document('52998224725') == '529.982.247-25'
    assert format_document('11222333000181') == '11.222.333/0001-81'
    assert format_cep('85000-000') == '85.000-000'
    assert format_cep('') == ''


def test_credentials_repr_hides_password():
    credentials = Credentials(user='11.222.333/0001-81', password='segredo-123')
    assert credentials.inscricao == '11222333000181'
    assert 'segredo-123' not in repr(credentials)
