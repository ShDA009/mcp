from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from exchangelib.errors import ErrorInvalidChangeKey, TransportError

from outlook_mcp.calendar_service import (
    find_free_slots,
    get_event_by_id,
    list_events_for_range,
)
from outlook_mcp.config import Config
from outlook_mcp.errors import ConnectionUnavailableError, ItemNotFoundError

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
    def __init__(self, start, end):
        self.start = start
        self.end = end


class FakeFreeBusyView:
    def __init__(self, working_hours, calendar_events):
        self.working_hours = working_hours
        self.calendar_events = calendar_events


class FreeSlotsAccount:
    def __init__(self):
        self.primary_smtp_address = "user@example.com"
        self.protocol = object()
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
