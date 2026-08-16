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


def test_translate_exceeded_find_count_limit():
    from exchangelib.errors import ErrorExceededFindCountLimit

    from outlook_mcp.errors import ResultTooLargeError

    result = translate_ews_error(ErrorExceededFindCountLimit("too many"))
    assert isinstance(result, ResultTooLargeError)
    assert result.to_dict()["error"] == "result_too_large"
    # сообщение должно подсказывать сузить диапазон, а не чинить сеть
    assert "narrow" in str(result).lower() or "range" in str(result).lower()


def test_translate_http_403_is_not_reported_as_network_problem():
    from exchangelib.errors import MalformedResponseError

    from outlook_mcp.errors import AuthenticationError as AuthErr

    exc = MalformedResponseError("Unknown failure in response. Code: 403 headers: {} content:")
    result = translate_ews_error(exc)
    assert isinstance(result, AuthErr)
    assert "403" in str(result)


def test_translate_transport_error_keeps_original_detail():
    result = translate_ews_error(TransportError("connection reset by peer"))
    assert isinstance(result, ConnectionUnavailableError)
    # исходная причина обязана попасть в сообщение, иначе диагностика вслепую
    assert "connection reset" in str(result)


def test_permission_denied_serializes():
    from outlook_mcp.errors import OutlookMcpError, PermissionDeniedError

    exc = PermissionDeniedError("Only the organizer can modify this event")
    assert isinstance(exc, OutlookMcpError)
    assert exc.to_dict() == {
        "error": "permission_denied",
        "message": "Only the organizer can modify this event",
    }
