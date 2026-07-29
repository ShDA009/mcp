from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from exchangelib.errors import ErrorInvalidChangeKey, TransportError

from outlook_mcp.calendar_service import (
    _busy_intervals_within,
    _HARD_BUSY_TYPES,
    _merge_intervals,
    _TENTATIVE_BUSY_TYPE,
    find_free_slots,
    get_event_by_id,
    list_events_for_range,
)
from outlook_mcp.config import Config
from outlook_mcp.errors import ConnectionUnavailableError, InvalidArgumentError, ItemNotFoundError

from .conftest import make_event, utc_dt


class FakeQuerySet(list):
    def order_by(self, *_args, **_kwargs):
        return self


class FakeCalendar:
    def __init__(self, items):
        self._items = items

    def filter(self, **_kwargs):
        return FakeQuerySet(self._items)

    def view(self, start, end, max_items=None):
        items = self._items if max_items is None else self._items[:max_items]
        return FakeQuerySet(items)


class FakeAccount:
    def __init__(self, items, fetch_result=None, fetch_error=None):
        self.calendar = FakeCalendar(items)
        self._fetch_result = fetch_result
        self._fetch_error = fetch_error

    def fetch(self, ids):
        if self._fetch_error is not None:
            raise self._fetch_error
        if self._fetch_result is None:
            return []
        return [self._fetch_result]


class RaisingCalendar:
    def filter(self, **_kwargs):
        raise TransportError("boom")

    def view(self, start, end, max_items=None):
        raise TransportError("boom")


class RaisingAccount:
    def __init__(self):
        self.calendar = RaisingCalendar()


def make_config():
    cfg = Config()
    cfg.timezone = "Europe/Moscow"
    cfg.default_limit = 50
    cfg.max_limit = 200
    return cfg


def test_list_events_for_range_normal():
    account = FakeAccount([make_event(subject="Standup")])
    result = list_events_for_range(account, date(2026, 7, 15), date(2026, 7, 15), make_config())
    assert len(result["events"]) == 1
    assert result["events"][0]["subject"] == "Standup"
    assert result["has_more"] is False


def test_list_events_for_range_empty_result():
    account = FakeAccount([])
    result = list_events_for_range(account, date(2026, 7, 15), date(2026, 7, 15), make_config())
    assert result["events"] == []
    assert result["has_more"] is False


def test_list_events_for_range_applies_limit():
    items = [make_event(subject=f"E{i}", item_id=f"id{i}") for i in range(5)]
    account = FakeAccount(items)
    result = list_events_for_range(
        account, date(2026, 7, 15), date(2026, 7, 15), make_config(), limit=2
    )
    assert len(result["events"]) == 2
    assert result["has_more"] is True


def test_list_events_for_range_translates_transport_errors():
    account = RaisingAccount()
    with pytest.raises(ConnectionUnavailableError):
        list_events_for_range(account, date(2026, 7, 15), date(2026, 7, 15), make_config())


def test_list_events_for_range_multi_day_range():
    items = [
        make_event(subject="Day1", start=utc_dt(2026, 7, 15, 10, 0), end=utc_dt(2026, 7, 15, 11, 0)),
        make_event(subject="Day3", start=utc_dt(2026, 7, 17, 9, 0), end=utc_dt(2026, 7, 17, 10, 0)),
    ]
    account = FakeAccount(items)
    result = list_events_for_range(account, date(2026, 7, 15), date(2026, 7, 17), make_config())
    assert len(result["events"]) == 2


def test_get_event_by_id_fetches_with_changekey():
    event = make_event(subject="Details", item_id="AAA", changekey="CCC")
    account = FakeAccount([], fetch_result=event)
    result = get_event_by_id(account, "AAA:CCC", make_config())
    assert result["subject"] == "Details"
    assert result["event_id"] == "AAA:CCC"


def test_get_event_by_id_falls_back_when_changekey_stale():
    event = make_event(subject="Recovered", item_id="AAA", changekey="NEWKEY")
    account = FakeAccount([event], fetch_error=ErrorInvalidChangeKey("stale"))
    result = get_event_by_id(account, "AAA:OLDKEY", make_config())
    assert result["subject"] == "Recovered"


def test_get_event_by_id_without_changekey_scans_calendar():
    event = make_event(subject="NoKey", item_id="AAA", changekey="X")
    account = FakeAccount([event])
    result = get_event_by_id(account, "AAA", make_config())
    assert result["subject"] == "NoKey"


def test_get_event_by_id_not_found_raises():
    account = FakeAccount([], fetch_result=None)
    with pytest.raises(ItemNotFoundError):
        get_event_by_id(account, "MISSING:KEY", make_config())


class FakeWorkingPeriod:
    def __init__(self, weekdays, start, end):
        self.weekdays = weekdays
        self.start = start
        self.end = end


class FakeCalendarEvent:
    def __init__(self, start, end, busy_type="Busy"):
        self.start = start
        self.end = end
        self.busy_type = busy_type


class FakeCalendarEventNoBusyType:
    """Mimics an odd/legacy CalendarEvent lacking the busy_type attribute."""

    def __init__(self, start, end):
        self.start = start
        self.end = end


class FakeFreeBusyView:
    def __init__(self, working_hours, calendar_events):
        self.working_hours = working_hours
        self.calendar_events = calendar_events


class FakeProtocol:
    """Scriptable protocol.get_free_busy_info for the "other participant" path.

    results_by_email maps email -> either a FakeFreeBusyView (success), an
    Exception instance to *raise* (mimics errors not in exchangelib's
    ERRORS_TO_CATCH_IN_RESPONSE, e.g. ErrorNoFreeBusyAccess), or an Exception
    instance wrapped in _Yielded to be *returned as a value* instead of raised
    (mimics ERRORS_TO_CATCH_IN_RESPONSE errors, e.g. ErrorMailRecipientNotFound).
    """

    def __init__(self, results_by_email=None):
        self.results_by_email = results_by_email or {}
        self.calls = []

    def get_free_busy_info(self, accounts, start, end, **_kwargs):
        email, _attendee_type, _exclude_conflicts = accounts[0]
        self.calls.append(email)
        result = self.results_by_email[email]
        if isinstance(result, _Yielded):
            return [result.exc]
        if isinstance(result, Exception):
            raise result
        return [result]


class _Yielded:
    def __init__(self, exc):
        self.exc = exc


class FreeSlotsAccount:
    def __init__(self, protocol=None):
        self.primary_smtp_address = "user@example.com"
        self.protocol = protocol or FakeProtocol()
        self.default_timezone = ZoneInfo("UTC")


def test_find_free_slots_returns_gaps_around_busy_event():
    working_hours = [FakeWorkingPeriod(["Wednesday"], time(9, 0), time(18, 0))]
    busy = [
        FakeCalendarEvent(
            utc_dt(2026, 7, 15, 7, 0),  # 10:00 Moscow
            utc_dt(2026, 7, 15, 8, 0),  # 11:00 Moscow
        )
    ]
    view = FakeFreeBusyView(working_hours, busy)

    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    slots = result["slots"]
    assert all(s["start"] < "2026-07-15T10:00:00+03:00" or s["start"] >= "2026-07-15T11:00:00+03:00" for s in slots)
    assert len(slots) > 0


def test_find_free_slots_no_working_hours_for_weekday_returns_empty():
    working_hours = [FakeWorkingPeriod(["Monday"], time(9, 0), time(18, 0))]
    view = FakeFreeBusyView(working_hours, [])

    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=view):
        # 2026-07-15 is a Wednesday, no working hours defined for it
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 30, make_config())

    assert result["slots"] == []


def test_find_free_slots_fully_booked_day_returns_empty():
    working_hours = [FakeWorkingPeriod(["Wednesday"], time(9, 0), time(18, 0))]
    busy = [FakeCalendarEvent(utc_dt(2026, 7, 15, 6, 0), utc_dt(2026, 7, 15, 15, 0))]
    view = FakeFreeBusyView(working_hours, busy)

    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 30, make_config())

    assert result["slots"] == []


def test_find_free_slots_translates_transport_errors():
    with patch(
        "outlook_mcp.calendar_service._get_free_busy_view", side_effect=TransportError("boom")
    ):
        with pytest.raises(ConnectionUnavailableError):
            find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 30, make_config())


def _own_view(busy=None):
    working_hours = [FakeWorkingPeriod(["Wednesday"], time(9, 0), time(18, 0))]
    return FakeFreeBusyView(working_hours, busy or [])


def test_find_free_slots_without_emails_keeps_previous_shape():
    account = FreeSlotsAccount()
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=_own_view()):
        result = find_free_slots(account, date(2026, 7, 15), 60, make_config())

    assert result["has_more"] is False
    assert result["unavailable"] == []
    assert account.protocol.calls == []


def test_find_free_slots_intersects_busy_of_other_participant():
    account = FreeSlotsAccount(
        protocol=FakeProtocol(
            {
                "colleague@example.com": FakeFreeBusyView(
                    [],
                    [FakeCalendarEvent(utc_dt(2026, 7, 15, 11, 0), utc_dt(2026, 7, 15, 12, 0))],  # 14:00-15:00 Moscow
                )
            }
        )
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=_own_view()):
        result = find_free_slots(
            account, date(2026, 7, 15), 60, make_config(), emails=["colleague@example.com"]
        )

    starts = [s["start"] for s in result["slots"]]
    assert "2026-07-15T14:00:00+03:00" not in starts
    assert "2026-07-15T09:00:00+03:00" in starts
    assert result["unavailable"] == []


def test_find_free_slots_own_mailbox_always_included():
    own_view = _own_view(busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 6, 0), utc_dt(2026, 7, 15, 15, 0))])
    account = FreeSlotsAccount(
        protocol=FakeProtocol({"colleague@example.com": FakeFreeBusyView([], [])})
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["colleague@example.com"]
        )

    assert result["slots"] == []


def test_find_free_slots_working_hours_come_from_own_mailbox():
    own_view = _own_view()  # 09:00-18:00 Wednesday
    colleague_view = FakeFreeBusyView(
        [FakeWorkingPeriod(["Wednesday"], time(9, 0), time(13, 0))], []
    )
    account = FreeSlotsAccount(protocol=FakeProtocol({"colleague@example.com": colleague_view}))
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(
            account, date(2026, 7, 15), 60, make_config(), emails=["colleague@example.com"]
        )

    starts = [s["start"] for s in result["slots"]]
    assert any(s >= "2026-07-15T13:00:00+03:00" for s in starts)


def test_find_free_slots_unavailable_email_is_skipped_not_fatal():
    from exchangelib.errors import ErrorNoFreeBusyAccess

    account = FreeSlotsAccount(
        protocol=FakeProtocol(
            {
                "bad@example.com": ErrorNoFreeBusyAccess("no access"),
                "good@example.com": FakeFreeBusyView(
                    [],
                    [FakeCalendarEvent(utc_dt(2026, 7, 15, 11, 0), utc_dt(2026, 7, 15, 12, 0))],
                ),
            }
        )
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=_own_view()):
        result = find_free_slots(
            account,
            date(2026, 7, 15),
            60,
            make_config(),
            emails=["bad@example.com", "good@example.com"],
        )

    assert result["unavailable"] == [{"email": "bad@example.com", "reason": ANY_STR}]
    starts = [s["start"] for s in result["slots"]]
    assert "2026-07-15T14:00:00+03:00" not in starts


class ANY_STR_TYPE:
    def __eq__(self, other):
        return isinstance(other, str) and len(other) > 0


ANY_STR = ANY_STR_TYPE()


def test_find_free_slots_unavailable_when_result_is_exception_instance():
    from exchangelib.errors import ErrorMailRecipientNotFound

    account = FreeSlotsAccount(
        protocol=FakeProtocol({"unknown@example.com": _Yielded(ErrorMailRecipientNotFound("nope"))})
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=_own_view()):
        result = find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["unknown@example.com"]
        )

    assert result["unavailable"] == [{"email": "unknown@example.com", "reason": ANY_STR}]


def test_find_free_slots_all_emails_unavailable_falls_back_to_own_calendar():
    from exchangelib.errors import ErrorNoFreeBusyAccess

    account = FreeSlotsAccount(
        protocol=FakeProtocol(
            {
                "a@example.com": ErrorNoFreeBusyAccess("no access"),
                "b@example.com": ErrorNoFreeBusyAccess("no access"),
            }
        )
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=_own_view()):
        alone = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 30, make_config())
        with_emails = find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["a@example.com", "b@example.com"]
        )

    assert with_emails["slots"] == alone["slots"]
    assert len(with_emails["unavailable"]) == 2


def test_find_free_slots_no_working_hours_skips_participant_requests():
    view = FakeFreeBusyView([FakeWorkingPeriod(["Monday"], time(9, 0), time(18, 0))], [])
    account = FreeSlotsAccount(protocol=FakeProtocol({"colleague@example.com": FakeFreeBusyView([], [])}))
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=view):
        # 2026-07-15 is Wednesday, no working hours defined for it
        result = find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["colleague@example.com"]
        )

    assert result == {"slots": [], "tentative_slots": [], "has_more": False, "unavailable": []}
    assert account.protocol.calls == []


def test_merge_intervals_unions_overlaps():
    a = datetime(2026, 7, 15, 9, tzinfo=ZoneInfo("UTC"))
    intervals = [
        (a, a + timedelta(hours=2)),  # 9-11
        (a + timedelta(hours=1), a + timedelta(hours=3)),  # 10-12
        (a + timedelta(hours=5), a + timedelta(hours=6)),  # 14-15
    ]
    merged = _merge_intervals(intervals)
    assert merged == [
        (a, a + timedelta(hours=3)),
        (a + timedelta(hours=5), a + timedelta(hours=6)),
    ]


def test_merge_intervals_merges_adjacent():
    a = datetime(2026, 7, 15, 9, tzinfo=ZoneInfo("UTC"))
    intervals = [(a, a + timedelta(hours=1)), (a + timedelta(hours=1), a + timedelta(hours=2))]
    assert _merge_intervals(intervals) == [(a, a + timedelta(hours=2))]


def test_get_free_busy_view_for_email_passes_plain_string():
    protocol = FakeProtocol({"colleague@example.com": FakeFreeBusyView([], [])})
    account = FreeSlotsAccount(protocol=protocol)
    start = utc_dt(2026, 7, 15, 0, 0)
    end = utc_dt(2026, 7, 15, 23, 59)

    from outlook_mcp.calendar_service import _get_free_busy_view_for_email

    _get_free_busy_view_for_email(account, "colleague@example.com", start, end)

    assert protocol.calls == ["colleague@example.com"]


# --- busy_type classification -------------------------------------------------


def test_find_free_slots_free_busy_type_does_not_block():
    own_view = _own_view(busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="Free")])
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    starts = [s["start"] for s in result["slots"]]
    assert "2026-07-15T10:00:00+03:00" in starts


def test_find_free_slots_working_elsewhere_does_not_block():
    own_view = _own_view(
        busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="WorkingElsewhere")]
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    starts = [s["start"] for s in result["slots"]]
    assert "2026-07-15T10:00:00+03:00" in starts


def test_find_free_slots_nodata_does_not_block():
    own_view = _own_view(busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="NoData")])
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    starts = [s["start"] for s in result["slots"]]
    assert "2026-07-15T10:00:00+03:00" in starts


def test_find_free_slots_oof_blocks():
    own_view = _own_view(busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="OOF")])
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    starts = [s["start"] for s in result["slots"]] + [s["start"] for s in result["tentative_slots"]]
    assert "2026-07-15T10:00:00+03:00" not in starts


def test_find_free_slots_busy_blocks():
    own_view = _own_view(busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="Busy")])
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    starts = [s["start"] for s in result["slots"]] + [s["start"] for s in result["tentative_slots"]]
    assert "2026-07-15T10:00:00+03:00" not in starts


def test_find_free_slots_missing_busy_type_treated_as_busy():
    own_view = _own_view(busy=[FakeCalendarEventNoBusyType(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0))])
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    starts = [s["start"] for s in result["slots"]] + [s["start"] for s in result["tentative_slots"]]
    assert "2026-07-15T10:00:00+03:00" not in starts


def test_busy_intervals_within_filters_by_busy_type():
    tz = ZoneInfo("Europe/Moscow")
    window_start = datetime(2026, 7, 15, 9, 0, tzinfo=tz)
    window_end = datetime(2026, 7, 15, 18, 0, tzinfo=tz)
    view = FakeFreeBusyView(
        [],
        [
            FakeCalendarEvent(utc_dt(2026, 7, 15, 6, 0), utc_dt(2026, 7, 15, 7, 0), busy_type="Busy"),
            FakeCalendarEvent(utc_dt(2026, 7, 15, 8, 0), utc_dt(2026, 7, 15, 9, 0), busy_type="Tentative"),
            FakeCalendarEvent(utc_dt(2026, 7, 15, 10, 0), utc_dt(2026, 7, 15, 11, 0), busy_type="Free"),
        ],
    )
    hard = _busy_intervals_within(view, window_start, window_end, tz, _HARD_BUSY_TYPES)
    assert len(hard) == 1

    tentative = _busy_intervals_within(view, window_start, window_end, tz, frozenset({_TENTATIVE_BUSY_TYPE}))
    assert len(tentative) == 1


# --- tentative_slots -----------------------------------------------------------


def test_find_free_slots_tentative_goes_to_tentative_slots():
    own_view = _own_view(
        busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="Tentative")]
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    slot_starts = [s["start"] for s in result["slots"]]
    tentative_starts = [s["start"] for s in result["tentative_slots"]]
    assert "2026-07-15T10:00:00+03:00" in tentative_starts
    assert "2026-07-15T10:00:00+03:00" not in slot_starts
    assert "2026-07-15T09:00:00+03:00" in slot_starts
    assert "2026-07-15T11:00:00+03:00" in slot_starts


def test_slots_and_tentative_slots_are_disjoint():
    own_view = _own_view(
        busy=[
            FakeCalendarEvent(utc_dt(2026, 7, 15, 6, 0), utc_dt(2026, 7, 15, 7, 0), busy_type="Busy"),
            FakeCalendarEvent(utc_dt(2026, 7, 15, 9, 0), utc_dt(2026, 7, 15, 10, 0), busy_type="Tentative"),
        ]
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    slot_starts = {s["start"] for s in result["slots"]}
    tentative_starts = {s["start"] for s in result["tentative_slots"]}
    assert slot_starts.isdisjoint(tentative_starts)


def test_tentative_does_not_shift_slot_grid():
    own_view = _own_view(
        busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 7, 30), busy_type="Tentative")]
    )  # 10:00-10:30 Moscow
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    slot_starts = [s["start"] for s in result["slots"]]
    tentative_starts = [s["start"] for s in result["tentative_slots"]]
    assert "2026-07-15T11:00:00+03:00" in slot_starts
    assert "2026-07-15T12:00:00+03:00" in slot_starts
    assert tentative_starts == ["2026-07-15T10:00:00+03:00"]


def test_tentative_partial_overlap_marks_slot_tentative():
    own_view = _own_view(
        busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 15), utc_dt(2026, 7, 15, 7, 45), busy_type="Tentative")]
    )  # 10:15-10:45 Moscow, inside slot 10:00-11:00
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    tentative_starts = [s["start"] for s in result["tentative_slots"]]
    assert "2026-07-15T10:00:00+03:00" in tentative_starts


def test_tentative_touching_slot_boundary_does_not_mark_it():
    own_view = _own_view(
        busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="Tentative")]
    )  # 10:00-11:00 Moscow, touches slot 11:00-12:00 boundary only
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(FreeSlotsAccount(), date(2026, 7, 15), 60, make_config())

    slot_starts = [s["start"] for s in result["slots"]]
    assert "2026-07-15T11:00:00+03:00" in slot_starts


def test_find_free_slots_tentative_of_other_participant_reported():
    account = FreeSlotsAccount(
        protocol=FakeProtocol(
            {
                "colleague@example.com": FakeFreeBusyView(
                    [],
                    [FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="Tentative")],
                )
            }
        )
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=_own_view()):
        result = find_free_slots(
            account, date(2026, 7, 15), 60, make_config(), emails=["colleague@example.com"]
        )

    tentative_starts = [s["start"] for s in result["tentative_slots"]]
    assert "2026-07-15T10:00:00+03:00" in tentative_starts


# --- include_self ----------------------------------------------------------


def test_find_free_slots_include_self_false_ignores_own_busy():
    own_view = _own_view(busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 6, 0), utc_dt(2026, 7, 15, 15, 0))])
    account = FreeSlotsAccount(
        protocol=FakeProtocol({"colleague@example.com": FakeFreeBusyView([], [])})
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        with_self = find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["colleague@example.com"], include_self=True
        )
        without_self = find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["colleague@example.com"], include_self=False
        )

    assert with_self["slots"] == []
    assert len(without_self["slots"]) > 0


def test_find_free_slots_include_self_false_still_uses_own_working_hours():
    own_view = _own_view()  # 09:00-18:00 Wednesday (from FakeWorkingPeriod, hardcoded 09-18)
    own_view.working_hours = [FakeWorkingPeriod(["Wednesday"], time(9, 0), time(13, 0))]
    colleague_view = FakeFreeBusyView([FakeWorkingPeriod(["Wednesday"], time(9, 0), time(18, 0))], [])
    account = FreeSlotsAccount(protocol=FakeProtocol({"colleague@example.com": colleague_view}))
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["colleague@example.com"], include_self=False
        )

    assert all(s["end"] <= "2026-07-15T13:00:00+03:00" for s in result["slots"])


def test_find_free_slots_include_self_false_still_fetches_own_view():
    account = FreeSlotsAccount(protocol=FakeProtocol({"colleague@example.com": FakeFreeBusyView([], [])}))
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=_own_view()) as mocked:
        find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["colleague@example.com"], include_self=False
        )
    mocked.assert_called_once()


def test_find_free_slots_include_self_false_own_view_failure_is_fatal():
    with patch(
        "outlook_mcp.calendar_service._get_free_busy_view", side_effect=TransportError("boom")
    ):
        with pytest.raises(ConnectionUnavailableError):
            find_free_slots(
                FreeSlotsAccount(),
                date(2026, 7, 15),
                30,
                make_config(),
                emails=["colleague@example.com"],
                include_self=False,
            )


def test_find_free_slots_include_self_false_without_emails_raises():
    account = FreeSlotsAccount()
    with pytest.raises(InvalidArgumentError):
        find_free_slots(account, date(2026, 7, 15), 30, make_config(), emails=None, include_self=False)
    with pytest.raises(InvalidArgumentError):
        find_free_slots(account, date(2026, 7, 15), 30, make_config(), emails=[], include_self=False)
    assert account.protocol.calls == []


def test_find_free_slots_include_self_false_ignores_own_tentative():
    own_view = _own_view(
        busy=[FakeCalendarEvent(utc_dt(2026, 7, 15, 7, 0), utc_dt(2026, 7, 15, 8, 0), busy_type="Tentative")]
    )
    account = FreeSlotsAccount(
        protocol=FakeProtocol({"colleague@example.com": FakeFreeBusyView([], [])})
    )
    with patch("outlook_mcp.calendar_service._get_free_busy_view", return_value=own_view):
        result = find_free_slots(
            account, date(2026, 7, 15), 30, make_config(), emails=["colleague@example.com"], include_self=False
        )

    assert result["tentative_slots"] == []
