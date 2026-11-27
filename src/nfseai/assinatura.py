"""Assinatura XMLDSig envelopada da DPS (e, depois, do pedido de registro de evento).

O Anexo I exige a assinatura com o certificado do emitente (regras E0714 a E0718), mas não fixa os
algoritmos. O perfil usado aqui é o do padrão NF-e, que bibliotecas em produção usam para a DPS:
C14N 1.0, transformação envelopada, SHA-1 e RSA-SHA1. Está marcado como "a confirmar" em
docs/caminho-a.md até a primeira emissão na produção restrita.

A ``Signature`` entra como último filho da raiz, depois do nó assinado, e referencia o ``Id`` dele.
"""
import base64
import hashlib

from lxml import etree

from .dps import NS
from .errors import NfseValidationError

DS = 'http://www.w3.org/2000/09/xmldsig#'
C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
ENVELOPED = f'{DS}enveloped-signature'
RSA_SHA1 = f'{DS}rsa-sha1'
DIGEST_SHA1 = f'{DS}sha1'

DECLARACAO = b'<?xml version="1.0" encoding="UTF-8"?>'


def _c14n(node):
    """C14N 1.0 inclusiva de um nó, como em um documento à parte.

    O libxml2, ao canonicalizar uma subárvore em memória, acrescenta ``xmlns=""`` aos filhos quando o
    namespace padrão muda no meio do documento (é o caso da ``Signature`` dentro da DPS). Os bytes
    ficariam diferentes dos que a Sefin calcula e a assinatura seria recusada (E0714). Serializar o nó
    mantém as declarações de namespace herdadas, então reler e canonicalizar dá a forma correta.
    """
    return etree.tostring(etree.fromstring(etree.tostring(node, with_tail=False)), method='c14n')


def _ds(parent, tag, text=None, **attrs):
    element = etree.SubElement(parent, f'{{{DS}}}{tag}', attrs)
    if text is not None:
        element.text = text
    return element


def assinar(xml, certificado, *, no='infDPS'):
    """Assina o filho ``no`` da raiz pelo ``Id`` dele e devolve o XML assinado em bytes."""
    raiz = etree.fromstring(xml)
    alvo = raiz.find(f'{{{NS}}}{no}')
    if alvo is None or not alvo.get('Id'):
        raise NfseValidationError(f'nó {no} com Id não encontrado para assinar', ['assinatura'])
    if raiz.find(f'{{{DS}}}Signature') is not None:
        raise NfseValidationError('o documento já está assinado', ['assinatura'])

    digest = base64.b64encode(hashlib.sha1(_c14n(alvo)).digest()).decode()  # noqa: S324

    assinatura = etree.SubElement(raiz, f'{{{DS}}}Signature', nsmap={None: DS})
    signed_info = _ds(assinatura, 'SignedInfo')
    _ds(signed_info, 'CanonicalizationMethod', Algorithm=C14N)
    _ds(signed_info, 'SignatureMethod', Algorithm=RSA_SHA1)
    referencia = _ds(signed_info, 'Reference', URI=f'#{alvo.get("Id")}')
    transforms = _ds(referencia, 'Transforms')
    _ds(transforms, 'Transform', Algorithm=ENVELOPED)
    _ds(transforms, 'Transform', Algorithm=C14N)
    _ds(referencia, 'DigestMethod', Algorithm=DIGEST_SHA1)
    _ds(referencia, 'DigestValue', digest)

    valor = certificado.assinar_rsa(_c14n(signed_info), 'sha1')
    _ds(assinatura, 'SignatureValue', base64.b64encode(valor).decode())
    x509 = _ds(_ds(assinatura, 'KeyInfo'), 'X509Data')
    _ds(x509, 'X509Certificate', base64.b64encode(certificado.certificado_der).decode())
    # A Sefin exige a declaração explícita de UTF-8 no documento enviado (E1229).
    return DECLARACAO + etree.tostring(raiz, encoding='utf-8', xml_declaration=False)


__all__ = ['assinar']
