from datetime import date
from unittest.mock import patch

from outlook_mcp import server
from outlook_mcp.errors import InvalidArgumentError

from .conftest import make_event


def test_parse_date_valid():
    assert server._parse_date("2026-07-15", "target_date") == date(2026, 7, 15)


def test_parse_date_none_returns_none():
    assert server._parse_date(None, "target_date") is None


def test_parse_date_invalid_raises_invalid_argument():
    result = _capture_error(lambda: server._parse_date("not-a-date", "target_date"))
    assert isinstance(result, InvalidArgumentError)


def _capture_error(fn):
    try:
        fn()
    except Exception as exc:
        return exc
    return None


def test_validate_limit_positive_passes():
    assert server._validate_limit(10) == 10


def test_validate_limit_none_passes():
    assert server._validate_limit(None) is None


def test_validate_limit_zero_raises():
    result = _capture_error(lambda: server._validate_limit(0))
    assert isinstance(result, InvalidArgumentError)


def test_validate_limit_negative_raises():
    result = _capture_error(lambda: server._validate_limit(-5))
    assert isinstance(result, InvalidArgumentError)


def test_list_events_invalid_date_returns_structured_error():
    with patch.object(server, "get_account", return_value=object()):
        result = server.list_events(target_date="not-a-date")
    assert result["error"] == "invalid_argument"


def test_list_events_happy_path_returns_events():
    fake_account = object()
    fake_result = {"events": [make_event()], "has_more": False}
    with patch.object(server, "get_account", return_value=fake_account), patch(
        "outlook_mcp.server.list_events_for_range", return_value=fake_result
    ):
        result = server.list_events(target_date="2026-07-15")
    assert result == fake_result


def test_get_event_not_found_returns_structured_error():
    from outlook_mcp.errors import ItemNotFoundError

    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.get_event_by_id", side_effect=ItemNotFoundError("nope")
    ):
        result = server.get_event(event_id="missing")
    assert result == {"error": "item_not_found", "message": "nope"}
