"""Certificado digital A1 (e-CNPJ ICP-Brasil) do caminho A.

O arquivo ``.pfx``/``.p12`` e a senha entram pelo terminal do usuário ou pelo ambiente, como a senha do portal
no caminho B. Este módulo abre o certificado em memória, confere validade e CNPJ e assina bytes. A senha não é
guardada no objeto, e nem a chave privada nem a senha aparecem em ``repr``, log ou mensagem de erro.

Depende do pacote ``cryptography``, instalado pelo extra ``nfseai[certificado]``.
"""
import datetime as dt
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from .config import only_digits
from .errors import CertificateError

OID_CNPJ = '2.16.76.1.3.3'  # OtherName do e-CNPJ ICP-Brasil com o CNPJ do titular
INSTALAR = (
    'suporte a certificado digital não instalado: rode `nfseai instalar-certificado` '
    '(ou `pip install "nfseai[certificado]"` no ambiente do nfseai)'
)


def _cryptography():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import pkcs12
    except ImportError:  # pragma: no cover - depende do ambiente
        raise CertificateError(INSTALAR) from None
    return x509, hashes, serialization, padding, pkcs12


def _cnpj_do_certificado(certificado, x509):
    try:
        san = certificado.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return ''
    for nome in san:
        if isinstance(nome, x509.OtherName) and nome.type_id.dotted_string == OID_CNPJ:
            match = re.search(rb'\d{14}', nome.value)
            if match:
                return match.group().decode()
    return ''


@dataclass(frozen=True)
class CertificadoA1:
    cnpj: str
    titular: str
    valido_de: dt.datetime
    valido_ate: dt.datetime
    certificado_pem: bytes = field(repr=False)
    certificado_der: bytes = field(repr=False)
    _chave: object = field(repr=False, compare=False)

    @classmethod
    def carregar(cls, origem, senha):
        """Abre o PKCS#12 a partir dos bytes ou do caminho do arquivo."""
        x509, _, serialization, _, pkcs12 = _cryptography()
        dados = origem if isinstance(origem, bytes) else Path(origem).expanduser().read_bytes()
        try:
            with warnings.catch_warnings():
                # O Windows exporta o .pfx em BER; a biblioteca abre e avisa que um dia pode recusar.
                # O aviso iria para a saída do comando; a conversão para DER fica registrada em docs/caminho-a.md.
                warnings.filterwarnings('ignore', message='.*could not be parsed as DER.*', category=UserWarning)
                chave, certificado, _ = pkcs12.load_key_and_certificates(dados, (senha or '').encode())
        except (ValueError, TypeError):
            raise CertificateError(
                'não foi possível abrir o certificado: senha incorreta ou arquivo que não é um A1 (.pfx/.p12)'
            ) from None
        if chave is None or certificado is None:
            raise CertificateError('o arquivo não traz a chave privada e o certificado do titular')
        nomes = certificado.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        return cls(
            cnpj=_cnpj_do_certificado(certificado, x509),
            titular=nomes[0].value if nomes else '',
            valido_de=certificado.not_valid_before_utc,
            valido_ate=certificado.not_valid_after_utc,
            certificado_pem=certificado.public_bytes(serialization.Encoding.PEM),
            certificado_der=certificado.public_bytes(serialization.Encoding.DER),
            _chave=chave,
        )

    def conferir(self, cnpj_emitente, agora=None):
        """Confere se o certificado vale agora e se é do CNPJ que emite a nota."""
        agora = agora or dt.datetime.now(dt.timezone.utc)
        if agora < self.valido_de:
            raise CertificateError(f'o certificado só passa a valer em {self.valido_de:%d/%m/%Y}')
        if agora > self.valido_ate:
            raise CertificateError(f'o certificado venceu em {self.valido_ate:%d/%m/%Y}; renove o e-CNPJ A1')
        if not self.cnpj:
            raise CertificateError(
                f'o certificado não traz o CNPJ ICP-Brasil (OID {OID_CNPJ}); use um e-CNPJ A1 da empresa'
            )
        if self.cnpj != only_digits(cnpj_emitente):
            raise CertificateError(
                f'o certificado é do CNPJ {self.cnpj}, mas a empresa emite pelo CNPJ {only_digits(cnpj_emitente)}'
            )
        return True

    def assinar_rsa(self, dados, algoritmo='sha1'):
        """Assinatura RSA PKCS#1 v1.5 dos bytes informados."""
        _, hashes, _, padding, _ = _cryptography()
        digest = {'sha1': hashes.SHA1(), 'sha256': hashes.SHA256()}[algoritmo]
        return self._chave.sign(dados, padding.PKCS1v15(), digest)

    def contexto_tls(self, pasta, cafile=None):
        """Contexto TLS com este certificado como certificado de cliente (TLS mútuo com a Sefin).

        O módulo ``ssl`` só lê chave e certificado de arquivo. Os dois são escritos em ``pasta`` com modo
        600, carregados e apagados em seguida, antes de qualquer conexão; a chave não fica em disco.
        """
        import os
        import ssl
        import tempfile

        _, _, serialization, _, _ = _cryptography()
        contexto = ssl.create_default_context(cafile=cafile)
        pasta = Path(pasta)
        pasta.mkdir(parents=True, exist_ok=True)
        fd, caminho = tempfile.mkstemp(prefix='.tls-', suffix='.pem', dir=str(pasta))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(self._chave.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                ))
                handle.write(self.certificado_pem)
            contexto.load_cert_chain(caminho)
        finally:
            if os.path.exists(caminho):
                os.unlink(caminho)
        return contexto


__all__ = ['OID_CNPJ', 'CertificadoA1']
