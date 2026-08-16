from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from exchangelib import EWSDate, EWSDateTime

from outlook_mcp.calendar_write import (
    create_event,
    delete_event,
    parse_date_only,
    parse_datetime,
    update_event,
)
from outlook_mcp.config import Config
from outlook_mcp.errors import (
    InvalidArgumentError,
    ItemNotFoundError,
    PermissionDeniedError,
)

from .conftest import (
    FakeAttendee,
    FakeMailbox,
    FakeWriteAccount,
    RecordingCalendarItem,
    make_writable_event,
    utc_dt,
)


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


def test_update_event_start_only_preserves_duration():
    item = make_writable_event(
        start=utc_dt(2026, 8, 20, 10, 0), end=utc_dt(2026, 8, 20, 11, 0)
    )
    account = FakeWriteAccount(item)
    update_event(account, make_config(), "AAA:CCC", start="2026-08-20T15:00")
    assert (item.end - item.start).total_seconds() == 3600
    assert item.start.astimezone(ZoneInfo("Europe/Moscow")).hour == 15


def test_update_event_end_only_changes_duration():
    item = make_writable_event(
        start=utc_dt(2026, 8, 20, 10, 0), end=utc_dt(2026, 8, 20, 11, 0)
    )
    original_start = item.start
    account = FakeWriteAccount(item)
    update_event(account, make_config(), "AAA:CCC", end="2026-08-20T17:00")
    assert item.start == original_start
    assert item.end.astimezone(ZoneInfo("Europe/Moscow")).hour == 17


def test_update_event_end_only_before_start_raises():
    item = make_writable_event(
        start=utc_dt(2026, 8, 20, 12, 0), end=utc_dt(2026, 8, 20, 13, 0)
    )
    account = FakeWriteAccount(item)
    with pytest.raises(InvalidArgumentError):
        # 09:00 MSK = 06:00 UTC, раньше существующего start (12:00 UTC)
        update_event(account, make_config(), "AAA:CCC", end="2026-08-20T09:00")


def test_update_event_both_bounds_taken_as_given():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    update_event(
        account,
        make_config(),
        "AAA:CCC",
        start="2026-08-20T15:00",
        end="2026-08-20T18:00",
    )
    assert (item.end - item.start).total_seconds() == 3 * 3600


def test_update_event_none_fields_are_untouched():
    item = make_writable_event(subject="Original")
    item.location = "Room 1"
    account = FakeWriteAccount(item)
    update_event(account, make_config(), "AAA:CCC", subject="Renamed")
    assert item.subject == "Renamed"
    assert item.location == "Room 1"


def test_update_event_rejects_empty_subject():
    account = FakeWriteAccount(make_writable_event())
    with pytest.raises(InvalidArgumentError):
        update_event(account, make_config(), "AAA:CCC", subject="  ")


def test_update_event_sends_invitations_by_default():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    result = update_event(account, make_config(), "AAA:CCC", subject="Moved")
    # Как в Outlook: уведомляются только затронутые изменением, а не все подряд
    assert item.saved_with["send_meeting_invitations"] == "SendToChangedAndSaveCopy"
    assert result["invitations_sent"] is True


def test_update_event_replaces_required_attendees():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    update_event(
        account, make_config(), "AAA:CCC", attendees=["b@example.com", "c@example.com"]
    )
    assert item.required_attendees == ["b@example.com", "c@example.com"]


def test_update_event_empty_attendees_clears_the_list():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    result = update_event(account, make_config(), "AAA:CCC", attendees=[])
    assert not item.required_attendees
    # Удалённый участник обязан получить отмену, хотя после правки список пуст
    assert result["invitations_sent"] is True
    assert item.saved_with["send_meeting_invitations"] == "SendToChangedAndSaveCopy"


def test_update_event_replaces_optional_attendees():
    item = make_writable_event()
    item.optional_attendees = [
        FakeAttendee(FakeMailbox(name="O", email_address="o@example.com"))
    ]
    account = FakeWriteAccount(item)
    update_event(account, make_config(), "AAA:CCC", optional_attendees=["x@example.com"])
    assert item.optional_attendees == ["x@example.com"]


def test_update_event_empty_optional_attendees_clears_the_list():
    item = make_writable_event()
    item.optional_attendees = [
        FakeAttendee(FakeMailbox(name="O", email_address="o@example.com"))
    ]
    account = FakeWriteAccount(item)
    update_event(account, make_config(), "AAA:CCC", optional_attendees=[])
    assert not item.optional_attendees


def test_update_event_omitted_attendees_are_untouched():
    item = make_writable_event()
    original = list(item.required_attendees)
    account = FakeWriteAccount(item)
    update_event(account, make_config(), "AAA:CCC", subject="Renamed")
    assert item.required_attendees == original


def test_update_event_clearing_attendees_on_solo_event_sends_nothing():
    item = make_writable_event(attendees=[])
    account = FakeWriteAccount(item)
    result = update_event(account, make_config(), "AAA:CCC", attendees=[])
    assert result["invitations_sent"] is False
    assert item.saved_with["send_meeting_invitations"] == "SendToNone"


def test_update_event_send_invitations_false():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    result = update_event(
        account, make_config(), "AAA:CCC", subject="Quiet", send_invitations=False
    )
    assert item.saved_with["send_meeting_invitations"] == "SendToNone"
    assert result["invitations_sent"] is False


def test_update_event_without_attendees_never_sends():
    item = make_writable_event(attendees=[])
    account = FakeWriteAccount(item)
    result = update_event(account, make_config(), "AAA:CCC", subject="Solo")
    assert item.saved_with["send_meeting_invitations"] == "SendToNone"
    assert result["invitations_sent"] is False


def test_update_event_occurrence_is_allowed():
    item = make_writable_event(item_type="Occurrence")
    account = FakeWriteAccount(item)
    update_event(account, make_config(), "AAA:CCC", subject="This one only")
    assert item.saved_with is not None


def test_update_event_recurring_master_rejected():
    item = make_writable_event(item_type="RecurringMaster")
    account = FakeWriteAccount(item)
    with pytest.raises(InvalidArgumentError) as excinfo:
        update_event(account, make_config(), "AAA:CCC", subject="Whole series")
    assert "series" in str(excinfo.value).lower()
    assert item.saved_with is None


def test_update_event_scope_series_rejected():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    with pytest.raises(InvalidArgumentError):
        update_event(account, make_config(), "AAA:CCC", subject="x", scope="series")
    assert item.saved_with is None


def test_update_event_non_organizer_rejected():
    item = make_writable_event(organizer_email="boss@example.com")
    account = FakeWriteAccount(item)
    with pytest.raises(PermissionDeniedError):
        update_event(account, make_config(), "AAA:CCC", subject="Not mine")
    assert item.saved_with is None


def test_update_event_organizer_match_is_case_insensitive():
    item = make_writable_event(organizer_email="Me@Example.COM")
    account = FakeWriteAccount(item)
    update_event(account, make_config(email="me@example.com"), "AAA:CCC", subject="Mine")
    assert item.saved_with is not None


def test_update_event_missing_item_raises_not_found():
    account = FakeWriteAccount(None)
    with pytest.raises(ItemNotFoundError):
        update_event(account, make_config(), "ZZZ:QQQ", subject="Ghost")


def test_update_event_stale_changekey_raises_with_hint():
    # Скан календаря убран: на ящике с бесконечными сериями он стоил десятки
    # секунд ради случая, который клиент чинит одним повторным list_events.
    item = make_writable_event(item_id="AAA", changekey="FRESH")
    account = FakeWriteAccount(None, calendar_items=[item])
    with pytest.raises(ItemNotFoundError) as excinfo:
        update_event(account, make_config(), "AAA:STALE", subject="Recovered")
    assert "list_events" in str(excinfo.value)
    assert item.saved_with is None


def test_delete_event_stale_changekey_raises_with_hint():
    item = make_writable_event(item_id="AAA", changekey="FRESH")
    account = FakeWriteAccount(None, calendar_items=[item])
    with pytest.raises(ItemNotFoundError):
        delete_event(account, make_config(), "AAA:STALE")
    assert item.deleted_with is None


def test_delete_event_sends_cancellations_by_default():
    item = make_writable_event(subject="Standup")
    account = FakeWriteAccount(item)
    result = delete_event(account, make_config(), "AAA:CCC")
    assert item.deleted_with["send_meeting_cancellations"] == "SendToAllAndSaveCopy"
    assert result == {
        "deleted": True,
        "event_id": "AAA:CCC",
        "subject": "Standup",
        "cancellations_sent": True,
    }


def test_delete_event_send_cancellations_false():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    result = delete_event(account, make_config(), "AAA:CCC", send_cancellations=False)
    assert item.deleted_with["send_meeting_cancellations"] == "SendToNone"
    assert result["cancellations_sent"] is False


def test_delete_event_without_attendees_never_sends():
    item = make_writable_event(attendees=[])
    account = FakeWriteAccount(item)
    result = delete_event(account, make_config(), "AAA:CCC")
    assert item.deleted_with["send_meeting_cancellations"] == "SendToNone"
    assert result["cancellations_sent"] is False


def test_delete_event_non_organizer_rejected():
    item = make_writable_event(organizer_email="boss@example.com")
    account = FakeWriteAccount(item)
    with pytest.raises(PermissionDeniedError):
        delete_event(account, make_config(), "AAA:CCC")
    assert item.deleted_with is None


def test_delete_event_recurring_master_rejected():
    item = make_writable_event(item_type="RecurringMaster")
    account = FakeWriteAccount(item)
    with pytest.raises(InvalidArgumentError):
        delete_event(account, make_config(), "AAA:CCC")
    assert item.deleted_with is None


def test_delete_event_occurrence_is_allowed():
    item = make_writable_event(item_type="Occurrence")
    account = FakeWriteAccount(item)
    delete_event(account, make_config(), "AAA:CCC")
    assert item.deleted_with is not None


def test_delete_event_scope_series_rejected():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    with pytest.raises(InvalidArgumentError):
        delete_event(account, make_config(), "AAA:CCC", scope="series")
    assert item.deleted_with is None


def test_delete_event_missing_item_raises_not_found():
    account = FakeWriteAccount(None)
    with pytest.raises(ItemNotFoundError):
        delete_event(account, make_config(), "ZZZ:QQQ")


def test_delete_event_ews_permission_error_translated():
    from exchangelib.errors import ErrorAccessDenied

    item = make_writable_event(delete_error=ErrorAccessDenied("nope"))
    account = FakeWriteAccount(item)
    with pytest.raises(PermissionDeniedError):
        delete_event(account, make_config(), "AAA:CCC")


def test_update_event_ews_permission_error_translated():
    from exchangelib.errors import ErrorCalendarIsNotOrganizer

    item = make_writable_event(save_error=ErrorCalendarIsNotOrganizer("nope"))
    account = FakeWriteAccount(item)
    with pytest.raises(PermissionDeniedError):
        update_event(account, make_config(), "AAA:CCC", subject="x")


def test_update_event_event_id_reread_after_save():
    item = make_writable_event(item_id="OLD", changekey="OLDCK")
    account = FakeWriteAccount(item)

    def bump_id(**_kwargs):
        item.id, item.changekey = "NEWID", "NEWCK"

    item.save = lambda **kwargs: bump_id(**kwargs)
    result = update_event(account, make_config(), "OLD:OLDCK", subject="Moved")
    # После обновления occurrence EWS может вернуть новый id — ответ должен
    # нести актуальный, иначе клиент потом не найдёт встречу.
    assert result["event_id"] == "NEWID:NEWCK"
