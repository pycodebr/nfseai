"""Exceções públicas do cliente do Emissor Nacional."""


class NfseError(Exception):
    """Erro base do cliente."""


class NfseAuthError(NfseError):
    """Falha de login, credenciais ausentes ou sessão expirada sem possibilidade de renovação."""


class NotConnectedError(NfseAuthError):
    """Não há credenciais do portal para a empresa: o usuário precisa rodar `nfseai conectar`."""


class ProfileError(NfseError):
    """Empresa (perfil) inexistente, nome inválido ou padrões fiscais incompletos."""


class FiscalProfileIncomplete(ProfileError):
    """Faltam dados fiscais do serviço (código, NBS, descrição, tributos) para montar a nota."""

    def __init__(self, message, missing=None):
        super().__init__(message)
        self.missing = list(missing or [])


class NfseHttpError(NfseError):
    """Resposta HTTP inesperada do portal."""

    def __init__(self, message, *, status_code=None, method=None, url=None):
        super().__init__(message)
        self.status_code = status_code
        self.method = method
        self.url = url


class ConfirmationRequired(NfseError):
    """Operação que altera estado fiscal chamada sem `confirm=True`."""


class NfseValidationError(NfseError):
    """O portal (ou a validação local) recusou os dados informados."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = list(errors or [])


class EmissionRejectedError(NfseValidationError):
    """O portal respondeu à requisição de emissão com erro, sem gerar a NFS-e."""


class UnsupportedScenario(NfseError):
    """Cenário fiscal ainda não mapeado neste cliente (ex.: retenção, exportação, IBS/CBS)."""


class PortalChangedError(NfseError):
    """O formulário do portal não corresponde mais ao contrato mapeado.

    Emitir com um formulário diferente do mapeado pode gerar nota errada, então o
    cliente interrompe antes de enviar qualquer dado.
    """


class CaptchaRequired(NfseError):
    """O recurso exige hCaptcha no portal web e não é automatizado por este cliente."""


class EmissionUncertainError(NfseError):
    """A requisição de emissão foi enviada, mas o resultado não pôde ser confirmado.

    Nunca repita a emissão automaticamente: confira as notas emitidas antes.
    """

    def __init__(self, message, *, draft_id=None, final_url=None, debug_path=None):
        super().__init__(message)
        self.draft_id = draft_id
        self.final_url = final_url
        self.debug_path = debug_path


class CancellationNotAllowed(NfseError):
    """A nota não pode ser cancelada diretamente (prazo, situação ou análise fiscal)."""

    def __init__(self, message, *, tipo_evento=None):
        super().__init__(message)
        self.tipo_evento = tipo_evento


class DuplicateReferenceError(NfseError):
    """A referência informada já tem emissão no registro local; a nota não é emitida de novo."""

    def __init__(self, message, *, status=None, chave_acesso=None):
        super().__init__(message)
        self.status = status
        self.chave_acesso = chave_acesso


class PdfUnavailableError(NfseError):
    """O PDF não pôde ser gerado (navegador ausente ou página de impressão indisponível)."""
