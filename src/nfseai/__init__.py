"""nfseai: NFS-e pelo Emissor Nacional (portal do contribuinte) para agentes de IA e scripts."""
from .client import EmissorNacionalClient
from .config import Credentials
from .errors import (
    CancellationNotAllowed,
    CaptchaRequired,
    ConfirmationRequired,
    DuplicateReferenceError,
    EmissionRejectedError,
    EmissionUncertainError,
    FiscalProfileIncomplete,
    NfseAuthError,
    NfseError,
    NfseHttpError,
    NfseValidationError,
    NotConnectedError,
    PdfUnavailableError,
    PortalChangedError,
    ProfileError,
    UnsupportedScenario,
)
from .ledger import Ledger
from .models import (
    MOTIVO_ERRO_EMISSAO,
    MOTIVO_OUTROS,
    MOTIVO_SERVICO_NAO_PRESTADO,
    EmissionRequest,
    Endereco,
    Servico,
    Tomador,
    Tributacao,
)
from .profiles import FiscalDefaults, Store
from .transport import PortalSession

__version__ = '0.1.1'

__all__ = [
    'MOTIVO_ERRO_EMISSAO',
    'MOTIVO_OUTROS',
    'MOTIVO_SERVICO_NAO_PRESTADO',
    'CancellationNotAllowed',
    'CaptchaRequired',
    'ConfirmationRequired',
    'Credentials',
    'DuplicateReferenceError',
    'EmissionRejectedError',
    'EmissionRequest',
    'EmissionUncertainError',
    'EmissorNacionalClient',
    'Endereco',
    'FiscalDefaults',
    'FiscalProfileIncomplete',
    'Ledger',
    'NfseAuthError',
    'NfseError',
    'NfseHttpError',
    'NfseValidationError',
    'NotConnectedError',
    'PdfUnavailableError',
    'PortalChangedError',
    'PortalSession',
    'ProfileError',
    'Servico',
    'Store',
    'Tomador',
    'Tributacao',
    'UnsupportedScenario',
]
