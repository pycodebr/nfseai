import datetime as dt

import pytest
from lxml import etree
from signxml import SignatureConfiguration, SignatureMethod, XMLVerifier
from signxml.algorithms import DigestAlgorithm

from certificado_teste import SENHA, gerar_pfx
from nfseai.assinatura import assinar
from nfseai.certificado import CertificadoA1
from nfseai.dps import validar_dps
from nfseai.errors import CertificateError
from test_dps import EMITTER, montar

SHA1 = SignatureConfiguration(
    signature_methods=frozenset({SignatureMethod.RSA_SHA1}),
    digest_algorithms=frozenset({DigestAlgorithm.SHA1}),
    require_x509=False,
)


@pytest.fixture(scope='module')
def certificado():
    return CertificadoA1.carregar(gerar_pfx(EMITTER), SENHA.decode())


def test_carrega_o_cnpj_da_extensao_icp_brasil(certificado):
    assert certificado.cnpj == EMITTER
    assert certificado.valido_ate > dt.datetime.now(dt.timezone.utc)


def test_senha_errada_vira_erro_de_certificado_sem_mostrar_a_senha():
    with pytest.raises(CertificateError) as exc:
        CertificadoA1.carregar(gerar_pfx(EMITTER), 'senha-errada-123')
    assert 'senha-errada-123' not in str(exc.value)


def test_repr_nao_expoe_chave_nem_senha(certificado):
    texto = repr(certificado)
    assert EMITTER in texto
    assert 'PRIVATE' not in texto and SENHA.decode() not in texto


def test_certificado_vencido_e_recusado():
    vencido = gerar_pfx(EMITTER, inicio=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc), dias_validade=30)
    certificado = CertificadoA1.carregar(vencido, SENHA.decode())
    with pytest.raises(CertificateError, match='venc'):
        certificado.conferir(EMITTER)


def test_certificado_de_outro_cnpj_e_recusado(certificado):
    with pytest.raises(CertificateError, match='CNPJ'):
        certificado.conferir('11444777000161')


def test_certificado_sem_cnpj_icp_brasil_e_recusado():
    certificado = CertificadoA1.carregar(gerar_pfx(EMITTER, com_cnpj=False), SENHA.decode())
    with pytest.raises(CertificateError, match='CNPJ'):
        certificado.conferir(EMITTER)


def test_dps_assinada_continua_valida_no_xsd(certificado):
    assinada = assinar(montar(), certificado)
    validar_dps(assinada)
    raiz = etree.fromstring(assinada)
    assert raiz[-1].tag == '{http://www.w3.org/2000/09/xmldsig#}Signature'


def test_assinatura_confere_em_verificador_independente(certificado):
    assinada = assinar(montar(), certificado)
    verificado = XMLVerifier().verify(assinada, x509_cert=certificado.certificado_pem, expect_config=SHA1)
    assinado = verificado.signed_xml
    assert assinado.get('Id').startswith('DPS')
    assert assinado.tag.endswith('infDPS')


def test_alterar_a_dps_depois_de_assinada_invalida_a_assinatura(certificado):
    assinada = assinar(montar(), certificado).replace(b'<vServ>1500.00</vServ>', b'<vServ>1.00</vServ>')
    with pytest.raises(Exception, match='[Dd]igest'):
        XMLVerifier().verify(assinada, x509_cert=certificado.certificado_pem, expect_config=SHA1)


def test_signed_info_canonicalizado_sem_xmlns_vazio(certificado):
    """Regressão: a C14N de subárvore do libxml2 punha xmlns="" nos filhos e a Sefin recusaria (E0714)."""
    from nfseai.assinatura import DS, _c14n

    signed_info = etree.fromstring(assinar(montar(), certificado)).find(f'{{{DS}}}Signature/{{{DS}}}SignedInfo')
    assert b'xmlns=""' not in _c14n(signed_info)


def test_pfx_exportado_pelo_windows_em_ber_abre_sem_aviso(monkeypatch, recwarn):
    """O Windows exporta o PKCS#12 em BER; a biblioteca abre, mas avisa. O aviso não pode vazar na saída JSON."""
    import warnings

    from cryptography.hazmat.primitives.serialization import pkcs12

    original = pkcs12.load_key_and_certificates

    def com_aviso(dados, senha):
        warnings.warn('PKCS#12 bundle could not be parsed as DER, falling back to parsing as BER.', UserWarning,
                      stacklevel=2)
        return original(dados, senha)

    monkeypatch.setattr(pkcs12, 'load_key_and_certificates', com_aviso)
    certificado = CertificadoA1.carregar(gerar_pfx(EMITTER), SENHA.decode())
    assert certificado.cnpj == EMITTER
    assert not [w for w in recwarn if 'BER' in str(w.message)]


def test_xml_assinado_declara_utf8():
    """Sem a declaração, a Sefin recusa com E1229 ("Xml não está utilizando codificação UTF-8")."""
    from test_dps import montar

    xml = assinar(montar(), CertificadoA1.carregar(gerar_pfx(EMITTER), SENHA.decode()))
    assert xml.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
