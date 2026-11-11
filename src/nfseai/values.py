"""Conversão de valores exibidos pelo portal."""
from decimal import Decimal, InvalidOperation


def clean(text):
    return ' '.join((text or '').split())


def parse_money(text):
    """'R$ 1.047,51' -> Decimal('1047.51'); devolve None para '-', vazio ou texto."""
    value = clean(text).replace('R$', '').replace('%', '').strip()
    if not value or value == '-':
        return None
    try:
        return Decimal(value.replace('.', '').replace(',', '.'))
    except InvalidOperation:
        return None


def format_money(value):
    """Decimal('1047.51') -> '1.047,51', como a máscara do portal envia."""
    quantized = Decimal(value).quantize(Decimal('0.01'))
    integer, _, cents = f'{quantized:.2f}'.partition('.')
    groups = []
    while len(integer) > 3:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    groups.insert(0, integer)
    return f'{".".join(groups)},{cents}'
