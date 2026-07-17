from exchangelib.errors import (
    ErrorItemNotFound,
    RateLimitError,
    TransportError,
    UnauthorizedError,
)

from outlook_mcp.errors import (
    AuthenticationError,
    ConnectionUnavailableError,
    ItemNotFoundError,
    ThrottlingError,
)
from outlook_mcp.ews_client import translate_ews_error


def test_translate_unauthorized_to_authentication_error():
    result = translate_ews_error(UnauthorizedError("bad creds"))
    assert isinstance(result, AuthenticationError)
    assert result.to_dict()["error"] == "authentication_error"


def test_translate_throttled_to_throttling_error():
    result = translate_ews_error(RateLimitError("slow down", wait=30))
    assert isinstance(result, ThrottlingError)


def test_translate_item_not_found():
    result = translate_ews_error(ErrorItemNotFound("nope"))
    assert isinstance(result, ItemNotFoundError)


def test_translate_transport_error_to_connection_unavailable():
    result = translate_ews_error(TransportError("no route"))
    assert isinstance(result, ConnectionUnavailableError)


def test_translate_unknown_error_passthrough():
    original = ValueError("weird")
    result = translate_ews_error(original)
    assert result is original
