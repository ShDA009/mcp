from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from exchangelib import EWSDate, EWSDateTime

from outlook_mcp.calendar_write import create_event, parse_date_only, parse_datetime
from outlook_mcp.config import Config
from outlook_mcp.errors import InvalidArgumentError

from .conftest import RecordingCalendarItem


def make_config(timezone="Europe/Moscow", email="me@example.com"):
    cfg = Config()
    cfg.timezone = timezone
    cfg.ews_email = email
    return cfg


def test_parse_datetime_naive_uses_config_timezone():
    result = parse_datetime("2026-08-20T15:00", "start", make_config())
    assert isinstance(result, EWSDateTime)
    assert (result.hour, result.minute) == (15, 0)
    assert result.utcoffset().total_seconds() == 3 * 3600


def test_parse_datetime_with_offset_is_respected():
    result = parse_datetime("2026-08-20T15:00:00+05:00", "start", make_config())
    # 15:00 +05:00 — это 10:00 UTC; момент времени сохраняется
    assert result.astimezone(ZoneInfo("UTC")).hour == 10


def test_parse_datetime_with_z_suffix():
    result = parse_datetime("2026-08-20T12:00:00Z", "start", make_config())
    assert result.astimezone(ZoneInfo("UTC")).hour == 12


def test_parse_datetime_invalid_raises_invalid_argument():
    with pytest.raises(InvalidArgumentError) as excinfo:
        parse_datetime("not-a-time", "start", make_config())
    assert "start" in str(excinfo.value)


def test_parse_datetime_date_only_raises():
    # Дата без времени для обычной встречи — ошибка; для all-day есть all_day=True
    with pytest.raises(InvalidArgumentError) as excinfo:
        parse_datetime("2026-08-20", "start", make_config())
    assert "all_day" in str(excinfo.value)


def test_parse_datetime_accepts_space_separated_time():
    result = parse_datetime("2026-08-20 15:00", "start", make_config())
    assert (result.hour, result.minute) == (15, 0)


def test_parse_datetime_accepts_explicit_midnight():
    result = parse_datetime("2026-08-20T00:00", "start", make_config())
    assert (result.hour, result.minute) == (0, 0)


def test_parse_date_only_accepts_date():
    assert parse_date_only("2026-08-20", "start") == EWSDate(2026, 8, 20)


def test_parse_date_only_accepts_datetime_string():
    # all_day=True: время игнорируется, берётся только календарный день
    assert parse_date_only("2026-08-20T15:00", "start") == EWSDate(2026, 8, 20)


def test_parse_date_only_invalid_raises():
    with pytest.raises(InvalidArgumentError):
        parse_date_only("20.08.2026", "start")


class FakeCreateAccount:
    """Account stub for create_event: only .calendar is touched."""

    def __init__(self):
        self.calendar = object()


def _make_item_factory(store):
    """Stand in for exchangelib.CalendarItem(...) inside create_event."""

    def factory(**kwargs):
        item = RecordingCalendarItem(
            id=None,
            changekey=None,
            subject=kwargs.get("subject"),
            start=kwargs.get("start"),
            end=kwargs.get("end"),
            location=kwargs.get("location"),
            body=kwargs.get("body"),
            is_all_day=kwargs.get("is_all_day", False),
            required_attendees=kwargs.get("required_attendees") or [],
            optional_attendees=kwargs.get("optional_attendees") or [],
        )
        item.init_kwargs = kwargs
        store.append(item)
        return item

    return factory


def test_create_event_sends_invitations_by_default():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        result = create_event(
            account,
            make_config(),
            subject="Sync",
            start="2026-08-20T15:00",
            end="2026-08-20T16:00",
            attendees=["a@example.com"],
        )
    assert store[0].saved_with["send_meeting_invitations"] == "SendToAllAndSaveCopy"
    assert result["invitations_sent"] is True


def test_create_event_without_attendees_never_sends():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        result = create_event(
            account,
            make_config(),
            subject="Focus time",
            start="2026-08-20T15:00",
            end="2026-08-20T16:00",
        )
    assert store[0].saved_with["send_meeting_invitations"] == "SendToNone"
    assert result["invitations_sent"] is False


def test_create_event_send_invitations_false():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        result = create_event(
            account,
            make_config(),
            subject="Draft",
            start="2026-08-20T15:00",
            end="2026-08-20T16:00",
            attendees=["a@example.com"],
            send_invitations=False,
        )
    assert store[0].saved_with["send_meeting_invitations"] == "SendToNone"
    assert result["invitations_sent"] is False


def test_create_event_all_day_uses_dates_and_flag():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        create_event(
            account,
            make_config(),
            subject="Vacation",
            start="2026-08-20",
            end="2026-08-22",
            all_day=True,
        )
    item = store[0]
    assert item.init_kwargs["is_all_day"] is True
    assert item.init_kwargs["start"] == EWSDate(2026, 8, 20)
    # end включительный для пользователя ("с 20 по 22")
    assert item.init_kwargs["end"] == EWSDate(2026, 8, 22)


def test_create_event_passes_attendees_as_email_strings():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        create_event(
            account,
            make_config(),
            subject="Sync",
            start="2026-08-20T15:00",
            end="2026-08-20T16:00",
            attendees=["a@example.com"],
            optional_attendees=["b@example.com"],
        )
    # AttendeesField принимает голые строки-email, exchangelib сам заворачивает
    assert store[0].init_kwargs["required_attendees"] == ["a@example.com"]
    assert store[0].init_kwargs["optional_attendees"] == ["b@example.com"]


def test_create_event_rejects_end_before_start():
    with pytest.raises(InvalidArgumentError):
        create_event(
            FakeCreateAccount(),
            make_config(),
            subject="Bad",
            start="2026-08-20T16:00",
            end="2026-08-20T15:00",
        )


def test_create_event_rejects_equal_start_end():
    with pytest.raises(InvalidArgumentError):
        create_event(
            FakeCreateAccount(),
            make_config(),
            subject="Bad",
            start="2026-08-20T15:00",
            end="2026-08-20T15:00",
        )


def test_create_event_rejects_empty_subject():
    with pytest.raises(InvalidArgumentError):
        create_event(
            FakeCreateAccount(),
            make_config(),
            subject="   ",
            start="2026-08-20T15:00",
            end="2026-08-20T16:00",
        )


def test_create_event_rejects_recurrence():
    with pytest.raises(InvalidArgumentError) as excinfo:
        create_event(
            FakeCreateAccount(),
            make_config(),
            subject="Daily",
            start="2026-08-20T15:00",
            end="2026-08-20T16:00",
            recurrence={"type": "daily"},
        )
    assert "recurring" in str(excinfo.value).lower()


def test_create_event_all_day_rejects_end_before_start():
    with pytest.raises(InvalidArgumentError):
        create_event(
            FakeCreateAccount(),
            make_config(),
            subject="Bad",
            start="2026-08-22",
            end="2026-08-20",
            all_day=True,
        )


def test_create_event_all_day_single_day_allowed():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        create_event(
            account,
            make_config(),
            subject="Day off",
            start="2026-08-20",
            end="2026-08-20",
            all_day=True,
        )
    assert store[0].init_kwargs["start"] == store[0].init_kwargs["end"]
