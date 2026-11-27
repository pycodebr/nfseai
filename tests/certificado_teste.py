"""Certificado A1 fictício para os testes do caminho A (gerado em memória, nunca gravado no repositório)."""
import datetime as dt

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

OID_CNPJ = x509.ObjectIdentifier('2.16.76.1.3.3')
SENHA = b'senha-de-teste'


def _octet_string(text):
    data = text.encode('ascii')
    return bytes([0x04, len(data)]) + data


def gerar_pfx(cnpj='11222333000181', *, dias_validade=365, inicio=None, senha=SENHA, com_cnpj=True):
    """PKCS#12 autoassinado com o CNPJ na extensão ICP-Brasil (OtherName 2.16.76.1.3.3)."""
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    inicio = inicio or dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'EMPRESA EXEMPLO LTDA:{cnpj}')])
    builder = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(inicio)
        .not_valid_after(inicio + dt.timedelta(days=dias_validade))
    )
    if com_cnpj:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.OtherName(OID_CNPJ, _octet_string(cnpj))]), critical=False
        )
    certificado = builder.sign(chave, hashes.SHA256())
    return pkcs12.serialize_key_and_certificates(
        b'teste', chave, certificado, None, serialization.BestAvailableEncryption(senha)
    )
