import logging

from exchangelib import Account, Configuration, Credentials, DELEGATE, NTLM
from exchangelib.errors import (
    ErrorItemNotFound,
    ErrorNonExistentMailbox,
    RateLimitError,
    TransportError,
    UnauthorizedError,
)

from .config import Config
from .errors import (
    AuthenticationError,
    ConnectionUnavailableError,
    ItemNotFoundError,
    ThrottlingError,
)

logger = logging.getLogger(__name__)


def build_account(config: Config) -> Account:
    credentials = Credentials(username=config.ews_username, password=config.ews_password)
    ews_config = Configuration(
        service_endpoint=config.ews_url,
        credentials=credentials,
        auth_type=NTLM,
    )
    try:
        return Account(
            primary_smtp_address=config.ews_email,
            config=ews_config,
            access_type=DELEGATE,
            autodiscover=False,
        )
    except (UnauthorizedError, ErrorNonExistentMailbox) as exc:
        logger.error("EWS authentication failed")
        raise AuthenticationError("Authentication with EWS failed") from exc
    except TransportError as exc:
        logger.error("EWS connection failed")
        raise ConnectionUnavailableError(
            "Could not reach EWS endpoint (check VPN/network)"
        ) from exc


def translate_ews_error(exc: Exception) -> Exception:
    if isinstance(exc, (UnauthorizedError, ErrorNonExistentMailbox)):
        return AuthenticationError("Authentication with EWS failed")
    if isinstance(exc, RateLimitError):
        return ThrottlingError("EWS is throttling requests, retry with backoff")
    if isinstance(exc, ErrorItemNotFound):
        return ItemNotFoundError("Requested item was not found")
    if isinstance(exc, TransportError):
        return ConnectionUnavailableError(
            "Could not reach EWS endpoint (check VPN/network)"
        )
    return exc
