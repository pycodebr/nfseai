"""Endereço do portal, credenciais em memória e formatação de documentos."""
import re
from dataclasses import dataclass, field

from .errors import NfseValidationError

BASE_URL = 'https://www.nfse.gov.br'
APP_PATH = '/EmissorNacional'
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/130.0.0.0 Safari/537.36'
)


@dataclass(frozen=True)
class Credentials:
    """Login do portal (CNPJ/CPF e senha). A senha nunca aparece em repr/log."""

    user: str
    password: str = field(repr=False)

    @property
    def inscricao(self):
        return only_digits(self.user)


def only_digits(value):
    return re.sub(r'\D', '', value or '')


def _cpf_is_valid(digits):
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        if (total * 10) % 11 % 10 != int(digits[size]):
            return False
    return True


def _cnpj_is_valid(digits):
    if len(digits) != 14 or digits == digits[0] * 14:
        return False
    for size in (12, 13):
        weights = list(range(size - 7, 1, -1)) + list(range(9, 1, -1))
        total = sum(int(d) * w for d, w in zip(digits[:size], weights, strict=True))
        check = 11 - total % 11
        if (0 if check >= 10 else check) != int(digits[size]):
            return False
    return True


def validate_document(value):
    """Devolve só os dígitos de um CPF/CNPJ válido ou lança NfseValidationError."""
    digits = only_digits(value)
    if len(digits) == 11 and _cpf_is_valid(digits):
        return digits
    if len(digits) == 14 and _cnpj_is_valid(digits):
        return digits
    raise NfseValidationError('CPF/CNPJ inválido', ['documento inválido'])


def format_document(digits):
    """Formata CPF/CNPJ como o portal envia nos formulários."""
    digits = only_digits(digits)
    if len(digits) == 11:
        return f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}'
    if len(digits) == 14:
        return f'{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}'
    raise NfseValidationError('documento com tamanho inesperado', ['documento inválido'])


def format_cep(value):
    digits = only_digits(value)
    if len(digits) != 8:
        return value or ''
    return f'{digits[:2]}.{digits[2:5]}-{digits[5:]}'
