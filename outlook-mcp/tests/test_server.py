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


def test_validate_emails_none_returns_empty_list():
    assert server._validate_emails(None) == []


def test_validate_emails_normalizes_and_dedupes():
    result = server._validate_emails([" A@X.RU ", "a@x.ru", "b@x.ru"])
    assert result == ["a@x.ru", "b@x.ru"]


def test_validate_emails_rejects_garbage():
    for bad in [[""], ["nope"], ["@x.ru"], ["a@"], [None]]:
        result = _capture_error(lambda bad=bad: server._validate_emails(bad))
        assert isinstance(result, InvalidArgumentError), bad


def test_validate_emails_rejects_too_many():
    emails = [f"user{i}@example.com" for i in range(21)]
    result = _capture_error(lambda: server._validate_emails(emails))
    assert isinstance(result, InvalidArgumentError)


def test_find_free_slots_tool_passes_emails_to_service():
    fake_result = {"slots": [], "tentative_slots": [], "has_more": False, "unavailable": [], "reason": "fully_busy"}
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.find_free_slots_svc", return_value=fake_result
    ) as svc:
        server.find_free_slots(target_date="2026-07-15", duration_min=30, emails=["a@x.ru"])
    _, kwargs = svc.call_args
    assert svc.call_args[0][:3] != ()  # positional args present
    assert kwargs["emails"] == ["a@x.ru"]


def test_find_free_slots_tool_invalid_email_returns_structured_error():
    with patch.object(server, "get_account", return_value=object()):
        result = server.find_free_slots(target_date="2026-07-15", duration_min=30, emails=["nope"])
    assert result["error"] == "invalid_argument"


def test_find_free_slots_tool_surfaces_unavailable_field():
    fake_result = {
        "slots": [],
        "tentative_slots": [],
        "has_more": False,
        "unavailable": [{"email": "bad@x.ru", "reason": "no access"}],
        "reason": "fully_busy",
    }
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.find_free_slots_svc", return_value=fake_result
    ):
        result = server.find_free_slots(target_date="2026-07-15", duration_min=30, emails=["bad@x.ru"])
    assert result == fake_result


def test_find_free_slots_tool_passes_include_self_to_service():
    fake_result = {"slots": [], "tentative_slots": [], "has_more": False, "unavailable": [], "reason": "fully_busy"}
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.find_free_slots_svc", return_value=fake_result
    ) as svc:
        server.find_free_slots(target_date="2026-07-15", duration_min=30, emails=["a@x.ru"], include_self=False)
    assert svc.call_args.kwargs["include_self"] is False


def test_find_free_slots_tool_include_self_defaults_true():
    fake_result = {"slots": [], "tentative_slots": [], "has_more": False, "unavailable": [], "reason": "fully_busy"}
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.find_free_slots_svc", return_value=fake_result
    ) as svc:
        server.find_free_slots(target_date="2026-07-15", duration_min=30)
    assert svc.call_args.kwargs["include_self"] is True


def test_find_free_slots_tool_surfaces_tentative_slots():
    fake_result = {
        "slots": [],
        "tentative_slots": [{"start": "2026-07-15T10:00:00+03:00", "end": "2026-07-15T11:00:00+03:00"}],
        "has_more": False,
        "unavailable": [],
        "reason": "only_tentative",
    }
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.find_free_slots_svc", return_value=fake_result
    ):
        result = server.find_free_slots(target_date="2026-07-15", duration_min=30)
    assert result == fake_result


def test_find_free_slots_tool_include_self_false_without_emails_returns_structured_error():
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.find_free_slots_svc", side_effect=InvalidArgumentError("include_self=false ...")
    ):
        result = server.find_free_slots(target_date="2026-07-15", duration_min=30, include_self=False)
    assert result["error"] == "invalid_argument"


def test_find_free_slots_tool_invalid_include_self_returns_structured_error():
    with patch.object(server, "get_account", return_value=object()):
        result = server.find_free_slots(target_date="2026-07-15", duration_min=30, include_self="yes")
    assert result["error"] == "invalid_argument"


def test_find_free_slots_tool_passes_debug_to_service():
    fake_result = {"slots": [], "tentative_slots": [], "has_more": False, "unavailable": [], "reason": "fully_busy"}
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.find_free_slots_svc", return_value=fake_result
    ) as svc:
        server.find_free_slots(target_date="2026-07-15", duration_min=30, debug=True)
    assert svc.call_args.kwargs["debug"] is True


def test_find_free_slots_tool_debug_defaults_false():
    fake_result = {"slots": [], "tentative_slots": [], "has_more": False, "unavailable": [], "reason": "fully_busy"}
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.find_free_slots_svc", return_value=fake_result
    ) as svc:
        server.find_free_slots(target_date="2026-07-15", duration_min=30)
    assert svc.call_args.kwargs["debug"] is False


def test_find_free_slots_tool_invalid_debug_returns_structured_error():
    with patch.object(server, "get_account", return_value=object()):
        result = server.find_free_slots(target_date="2026-07-15", duration_min=30, debug="yes")
    assert result["error"] == "invalid_argument"


def test_resolve_person_happy_path_returns_candidates():
    fake_account = object()
    fake_result = {"candidates": [{"name": "Ivanov Ivan", "email": "i.ivanov@example.com"}]}
    with patch.object(server, "get_account", return_value=fake_account), patch(
        "outlook_mcp.server.resolve_person_svc", return_value=fake_result
    ) as svc:
        result = server.resolve_person(query="Ivanov Ivan")
    assert result == fake_result
    svc.assert_called_once_with(fake_account, "Ivanov Ivan")


def test_resolve_person_empty_query_returns_structured_error():
    with patch.object(server, "get_account", return_value=object()):
        result = server.resolve_person(query="   ")
    assert result["error"] == "invalid_argument"


def test_resolve_person_no_matches_returns_empty_candidates():
    with patch.object(server, "get_account", return_value=object()), patch(
        "outlook_mcp.server.resolve_person_svc", return_value={"candidates": []}
    ):
        result = server.resolve_person(query="Nobody")
    assert result == {"candidates": []}
