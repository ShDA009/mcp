# Calendar Write Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в `outlook-mcp` три пишущих MCP-tool — `create_event`, `update_event`, `delete_event` — сняв read-only только для календаря.

**Architecture:** Вся логика записи — в новом модуле `src/outlook_mcp/calendar_write.py`; `calendar_service.py` (574 строки, занят алгоритмом `find_free_slots`) не трогаем, кроме импорта уже существующих в нём `_fetch_one`/`_find_by_id_in_calendar`. Tools в `server.py` регистрируются условно по флагу `Config.allow_write`. Формат ответа совпадает с `get_event` (`format_event_details`), ошибки — как везде: tool ловит `OutlookMcpError` и возвращает `exc.to_dict()`.

**Tech Stack:** Python 3.14, exchangelib 5.6.0, mcp (FastMCP), pytest, uv.

**Спек:** `docs/superpowers/specs/2026-08-15-calendar-write-design.md`

## Global Constraints

- **exchangelib строго 5.6.0** — все имена ниже проверены на этой версии; при апгрейде перепроверить.
- **Проверенные контракты exchangelib** (проверено вживую перед написанием плана, не менять по памяти):
  - `Item.save(update_fields=None, conflict_resolution='AutoResolve', send_meeting_invitations='SendToNone')` — один и тот же метод для создания и обновления, ветвится по наличию `self.id`.
  - `Item.delete(send_meeting_cancellations='SendToNone', affected_task_occurrences='AllOccurrences', suppress_read_receipts=True)`.
  - Константы: `SEND_TO_NONE = 'SendToNone'`, `SEND_TO_ALL_AND_SAVE_COPY = 'SendToAllAndSaveCopy'` — импортируются из `exchangelib.items`.
  - `CalendarItem.organizer` — **read-only поле**, присваивать нельзя.
  - `CalendarItem.type` — read-only, значения `Single`/`Occurrence`/`Exception`/`RecurringMaster`.
  - `required_attendees`/`optional_attendees` — `AttendeesField`, **принимает список строк-email** (exchangelib сам заворачивает в `Attendee`).
  - `start`/`end` — `DateOrDateTimeField`: `EWSDateTime` для обычных встреч, `EWSDate` для all-day.
  - **Ошибки прав в 5.6.0**: `ErrorCalendarIsNotOrganizer`, `ErrorAccessDenied`, `ErrorCalendarCannotUpdateDeletedItem`, `ErrorCannotDeleteObject`. Класса `ErrorCannotUpdateObject` в этой версии **нет** (в спеке он назван ошибочно — использовать список отсюда).
  - После `save()` обновления occurrence **id item'а может измениться** (см. комментарий про `OccurrenceItemId` в `Item.save`) — `event_id` в ответе брать с item после save, не переиспользовать входной.
- **Таймзона:** naive-строка читается как локальное время `config.timezone`; строка с offset уважается. Для EWS использовать `EWSTimeZone.from_zoneinfo(ZoneInfo(config.timezone))`, не голый `ZoneInfo`.
- **Ошибки не пробрасываются через MCP:** каждый tool ловит `OutlookMcpError` → `exc.to_dict()`.
- **Фейки в тестах обязаны повторять реальный контракт exchangelib** (главный урок проекта: баг с `WorkingPeriod.weekdays` не поймали ~60 зелёных тестов из-за фейка с удобным представлением).
- **Комментарии в коде** — только там, где объясняют неочевидное «почему»; язык файла — как в соседних модулях (английский в коде, русский в CLAUDE.md).
- Запуск тестов: `uv run pytest` из `outlook-mcp/`.

---

## File Structure

| Файл | Ответственность |
|---|---|
| `src/outlook_mcp/config.py` | + `allow_write: bool` из `EWS_ALLOW_WRITE` (дефолт `True`) |
| `src/outlook_mcp/errors.py` | + `PermissionDeniedError` |
| `src/outlook_mcp/calendar_write.py` | **новый** — парсинг времени, валидация, `create_event`/`update_event`/`delete_event` |
| `src/outlook_mcp/server.py` | + три tool, условная регистрация, `_validate_emails` переиспользуется |
| `tests/conftest.py` | + фейки записи (`save`/`delete` c записью kwargs) |
| `tests/test_calendar_write.py` | **новый** — тесты трёх операций |
| `tests/test_config.py` | + тесты `allow_write` |
| `tests/test_server.py` | + тест условной регистрации |
| `README.md`, `CLAUDE.md`, `install/README.md` | документация |

---

## Task 1: Флаг `EWS_ALLOW_WRITE` в Config

**Files:**
- Modify: `src/outlook_mcp/config.py:47-57`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `Config._get` (локальная функция в `__init__`), `ConfigError`
- Produces: `Config.allow_write: bool` — читают Task 7 (регистрация tools)

- [ ] **Step 1: Написать падающие тесты**

В конец `tests/test_config.py`:

```python
def test_allow_write_defaults_to_true(monkeypatch):
    monkeypatch.delenv("EWS_ALLOW_WRITE", raising=False)
    assert Config().allow_write is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "No", "off"])
def test_allow_write_disabled_by_falsy_values(monkeypatch, raw):
    monkeypatch.setenv("EWS_ALLOW_WRITE", raw)
    assert Config().allow_write is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", ""])
def test_allow_write_enabled_by_other_values(monkeypatch, raw):
    monkeypatch.setenv("EWS_ALLOW_WRITE", raw)
    assert Config().allow_write is True
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_config.py -k allow_write -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'allow_write'`

- [ ] **Step 3: Реализовать**

В `Config.__init__`, после `self.max_limit = ...`:

```python
        # Пишущие tools по умолчанию включены; переменная существует, чтобы
        # можно было раздать заведомо read-only сервер, не меняя код.
        self.allow_write = _get("EWS_ALLOW_WRITE", "1").strip().lower() not in _FALSY
```

Рядом с `_ENV_FILE` (модульный уровень):

```python
_FALSY = {"0", "false", "no", "off"}
```

Пустая строка не входит в `_FALSY` — она означает «переменная задана пустой», что трактуем как дефолт (включено).

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (все, включая существующие)

- [ ] **Step 5: Commit**

```bash
git add src/outlook_mcp/config.py tests/test_config.py
git commit -m "feat(outlook-mcp): флаг EWS_ALLOW_WRITE в Config"
```

---

## Task 2: `PermissionDeniedError`

**Files:**
- Modify: `src/outlook_mcp/errors.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `PermissionDeniedError(OutlookMcpError)` с `code = "permission_denied"` — используют Task 5, 6

- [ ] **Step 1: Написать падающий тест**

В конец `tests/test_errors.py`:

```python
def test_permission_denied_serializes():
    from outlook_mcp.errors import OutlookMcpError, PermissionDeniedError

    exc = PermissionDeniedError("Only the organizer can modify this event")
    assert isinstance(exc, OutlookMcpError)
    assert exc.to_dict() == {
        "error": "permission_denied",
        "message": "Only the organizer can modify this event",
    }
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/test_errors.py::test_permission_denied_serializes -v`
Expected: FAIL — `ImportError: cannot import name 'PermissionDeniedError'`

- [ ] **Step 3: Реализовать**

В конец `src/outlook_mcp/errors.py`:

```python
class PermissionDeniedError(OutlookMcpError):
    code = "permission_denied"
```

- [ ] **Step 4: Убедиться, что тест проходит**

Run: `uv run pytest tests/test_errors.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/outlook_mcp/errors.py tests/test_errors.py
git commit -m "feat(outlook-mcp): доменная ошибка PermissionDeniedError"
```

---

## Task 3: Парсинг времени и фейки записи

Фундамент для трёх операций: разбор ISO-строк с учётом таймзоны конфига и тестовые фейки, повторяющие контракт exchangelib.

**Files:**
- Create: `src/outlook_mcp/calendar_write.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_calendar_write.py` (создать)

**Interfaces:**
- Consumes: `Config.timezone`, `InvalidArgumentError`
- Produces:
  - `parse_datetime(value: str, field_name: str, config: Config) -> EWSDateTime`
  - `parse_date_only(value: str, field_name: str) -> EWSDate`
  - conftest: `RecordingCalendarItem`, `make_writable_event(...)`, `FakeWriteAccount`
  - Это используют Task 4, 5, 6.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_calendar_write.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from exchangelib import EWSDate, EWSDateTime

from outlook_mcp.calendar_write import parse_date_only, parse_datetime
from outlook_mcp.config import Config
from outlook_mcp.errors import InvalidArgumentError


def make_config(timezone="Europe/Moscow"):
    cfg = Config()
    cfg.timezone = timezone
    return cfg


def test_parse_datetime_naive_uses_config_timezone():
    result = parse_datetime("2026-08-20T15:00", "start", make_config())
    assert isinstance(result, EWSDateTime)
    assert (result.hour, result.minute) == (15, 0)
    assert result.utcoffset().total_seconds() == 3 * 3600


def test_parse_datetime_with_offset_is_respected():
    result = parse_datetime("2026-08-20T15:00:00+05:00", "start", make_config())
    # 15:00 +05:00 — это 13:00 по Москве; момент времени сохраняется
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
    with pytest.raises(InvalidArgumentError):
        parse_datetime("2026-08-20", "start", make_config())


def test_parse_date_only_accepts_date():
    assert parse_date_only("2026-08-20", "start") == EWSDate(2026, 8, 20)


def test_parse_date_only_accepts_datetime_string():
    # all_day=True: время игнорируется, берётся только календарный день
    assert parse_date_only("2026-08-20T15:00", "start") == EWSDate(2026, 8, 20)


def test_parse_date_only_invalid_raises():
    with pytest.raises(InvalidArgumentError):
        parse_date_only("20.08.2026", "start")
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_calendar_write.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'outlook_mcp.calendar_write'`

- [ ] **Step 3: Реализовать парсинг**

Создать `src/outlook_mcp/calendar_write.py`:

```python
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from exchangelib import EWSDate, EWSDateTime, EWSTimeZone

from .config import Config
from .errors import InvalidArgumentError

logger = logging.getLogger(__name__)


def parse_datetime(value: str, field_name: str, config: Config) -> EWSDateTime:
    """Parse an ISO-8601 string into an EWSDateTime.

    A naive string ("2026-08-20T15:00") is read as wall-clock time in the
    configured timezone - that is what a user means by "at 15:00", and making
    the LLM guess a UTC offset would silently shift the meeting by hours.
    A string carrying an offset is respected as-is.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidArgumentError(
            f"Invalid {field_name} {value!r}, expected an ISO datetime like 2026-08-20T15:00"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidArgumentError(
            f"Invalid {field_name} {value!r}, expected an ISO datetime like 2026-08-20T15:00"
        ) from exc

    if parsed.time() == datetime.min.time() and "T" not in value and " " not in value.strip():
        raise InvalidArgumentError(
            f"Invalid {field_name} {value!r}: no time of day. "
            "Pass a time (2026-08-20T15:00) or set all_day=true"
        )

    tz = EWSTimeZone.from_zoneinfo(ZoneInfo(config.timezone))
    if parsed.tzinfo is None:
        return EWSDateTime.from_datetime(parsed.replace(tzinfo=tz))
    return EWSDateTime.from_datetime(parsed).astimezone(tz)


def parse_date_only(value: str, field_name: str) -> EWSDate:
    """Parse the calendar day out of an ISO string, ignoring any time part."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidArgumentError(
            f"Invalid {field_name} {value!r}, expected a date like 2026-08-20"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidArgumentError(
            f"Invalid {field_name} {value!r}, expected a date like 2026-08-20"
        ) from exc
    return EWSDate(parsed.year, parsed.month, parsed.day)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_calendar_write.py -v`
Expected: PASS (9 тестов)

- [ ] **Step 5: Добавить фейки записи в conftest**

В конец `tests/conftest.py`:

```python
class RecordingCalendarItem(FakeCalendarItem):
    """Fake CalendarItem that records how save()/delete() were called.

    Mirrors the real exchangelib contract: one save() for both create and
    update (it branches on self.id), and the same kwarg names. Tests assert
    on the recorded kwargs, not just on the outcome.
    """

    def __init__(self, *args, **kwargs):
        self.saved_with = None
        self.deleted_with = None
        self.save_error = kwargs.pop("save_error", None)
        self.delete_error = kwargs.pop("delete_error", None)
        super().__init__(*args, **kwargs)

    def save(self, update_fields=None, conflict_resolution="AutoResolve",
             send_meeting_invitations="SendToNone"):
        if self.save_error is not None:
            raise self.save_error
        self.saved_with = {
            "update_fields": update_fields,
            "conflict_resolution": conflict_resolution,
            "send_meeting_invitations": send_meeting_invitations,
        }
        if not self.id:
            self.id = "NEW-ID"
            self.changekey = "NEW-CK"
        return self

    def delete(self, send_meeting_cancellations="SendToNone",
               affected_task_occurrences="AllOccurrences", suppress_read_receipts=True):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted_with = {"send_meeting_cancellations": send_meeting_cancellations}


def make_writable_event(
    subject="Sync",
    start=None,
    end=None,
    organizer_email="me@example.com",
    item_id="AAA",
    changekey="CCC",
    item_type="Single",
    is_all_day=False,
    save_error=None,
    delete_error=None,
):
    """Build a RecordingCalendarItem owned by me@example.com by default."""
    return RecordingCalendarItem(
        id=item_id,
        changekey=changekey,
        subject=subject,
        start=start or utc_dt(2026, 8, 20, 10, 0),
        end=end or utc_dt(2026, 8, 20, 11, 0),
        organizer=FakeMailbox(name="Me", email_address=organizer_email),
        type=item_type,
        is_all_day=is_all_day,
        save_error=save_error,
        delete_error=delete_error,
    )


class FakeWriteAccount:
    """Account stub for write paths: fetch() returns a prepared item."""

    def __init__(self, item=None, fetch_error=None):
        self._item = item
        self._fetch_error = fetch_error
        self.calendar = None

    def fetch(self, ids):
        if self._fetch_error is not None:
            raise self._fetch_error
        return [] if self._item is None else [self._item]
```

- [ ] **Step 6: Проверить, что conftest не сломал существующие тесты**

Run: `uv run pytest -q`
Expected: PASS — все существующие тесты зелёные, новых падений нет

- [ ] **Step 7: Commit**

```bash
git add src/outlook_mcp/calendar_write.py tests/test_calendar_write.py tests/conftest.py
git commit -m "feat(outlook-mcp): парсинг времени для записи в календарь + фейки"
```

---

## Task 4: `create_event` (сервисный слой)

**Files:**
- Modify: `src/outlook_mcp/calendar_write.py`
- Test: `tests/test_calendar_write.py`

**Interfaces:**
- Consumes: `parse_datetime`, `parse_date_only` (Task 3), `format_event_details`, `translate_ews_error`
- Produces: `create_event(account, config, *, subject, start, end, attendees=None, optional_attendees=None, location=None, body=None, all_day=False, send_invitations=True, recurrence=None) -> dict` — использует Task 7

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_calendar_write.py` (импорты дополнить сверху):

```python
from unittest.mock import patch

from outlook_mcp.calendar_write import create_event

from .conftest import RecordingCalendarItem, make_writable_event


class FakeCreateAccount:
    def __init__(self):
        self.created = []


def _make_item_factory(store):
    """Stand in for exchangelib.CalendarItem(...) inside create_event."""

    def factory(**kwargs):
        item = RecordingCalendarItem(
            id=None, changekey=None,
            subject=kwargs.get("subject"),
            start=kwargs.get("start"), end=kwargs.get("end"),
            location=kwargs.get("location"),
            is_all_day=kwargs.get("is_all_day", False),
        )
        item.required_attendees = kwargs.get("required_attendees") or []
        item.optional_attendees = kwargs.get("optional_attendees") or []
        item.init_kwargs = kwargs
        store.append(item)
        return item

    return factory


def test_create_event_sends_invitations_by_default():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        result = create_event(
            account, make_config(),
            subject="Sync", start="2026-08-20T15:00", end="2026-08-20T16:00",
            attendees=["a@example.com"],
        )
    assert store[0].saved_with["send_meeting_invitations"] == "SendToAllAndSaveCopy"
    assert result["invitations_sent"] is True


def test_create_event_without_attendees_never_sends():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        result = create_event(
            account, make_config(),
            subject="Focus time", start="2026-08-20T15:00", end="2026-08-20T16:00",
        )
    assert store[0].saved_with["send_meeting_invitations"] == "SendToNone"
    assert result["invitations_sent"] is False


def test_create_event_send_invitations_false():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        result = create_event(
            account, make_config(),
            subject="Draft", start="2026-08-20T15:00", end="2026-08-20T16:00",
            attendees=["a@example.com"], send_invitations=False,
        )
    assert store[0].saved_with["send_meeting_invitations"] == "SendToNone"
    assert result["invitations_sent"] is False


def test_create_event_all_day_uses_dates_and_flag():
    account, store = FakeCreateAccount(), []
    with patch("outlook_mcp.calendar_write.CalendarItem", _make_item_factory(store)):
        create_event(
            account, make_config(),
            subject="Vacation", start="2026-08-20", end="2026-08-22", all_day=True,
        )
    item = store[0]
    assert item.init_kwargs["is_all_day"] is True
    assert item.init_kwargs["start"] == EWSDate(2026, 8, 20)
    # end включительный для пользователя ("с 20 по 22")
    assert item.init_kwargs["end"] == EWSDate(2026, 8, 22)


def test_create_event_rejects_end_before_start():
    with pytest.raises(InvalidArgumentError):
        create_event(
            FakeCreateAccount(), make_config(),
            subject="Bad", start="2026-08-20T16:00", end="2026-08-20T15:00",
        )


def test_create_event_rejects_equal_start_end():
    with pytest.raises(InvalidArgumentError):
        create_event(
            FakeCreateAccount(), make_config(),
            subject="Bad", start="2026-08-20T15:00", end="2026-08-20T15:00",
        )


def test_create_event_rejects_empty_subject():
    with pytest.raises(InvalidArgumentError):
        create_event(
            FakeCreateAccount(), make_config(),
            subject="   ", start="2026-08-20T15:00", end="2026-08-20T16:00",
        )


def test_create_event_rejects_recurrence():
    with pytest.raises(InvalidArgumentError) as excinfo:
        create_event(
            FakeCreateAccount(), make_config(),
            subject="Daily", start="2026-08-20T15:00", end="2026-08-20T16:00",
            recurrence={"type": "daily"},
        )
    assert "recurring" in str(excinfo.value).lower()


def test_create_event_all_day_rejects_end_before_start():
    with pytest.raises(InvalidArgumentError):
        create_event(
            FakeCreateAccount(), make_config(),
            subject="Bad", start="2026-08-22", end="2026-08-20", all_day=True,
        )
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_calendar_write.py -k create_event -v`
Expected: FAIL — `ImportError: cannot import name 'create_event'`

- [ ] **Step 3: Реализовать**

Дописать в `src/outlook_mcp/calendar_write.py` (импорты сверху дополнить):

```python
from exchangelib import CalendarItem
from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY, SEND_TO_NONE

from .ews_client import translate_ews_error
from .formatting import format_event_details
```

```python
def create_event(
    account,
    config: Config,
    *,
    subject: str,
    start: str,
    end: str,
    attendees: list[str] | None = None,
    optional_attendees: list[str] | None = None,
    location: str | None = None,
    body: str | None = None,
    all_day: bool = False,
    send_invitations: bool = True,
    recurrence: dict | None = None,
) -> dict:
    if recurrence is not None:
        raise InvalidArgumentError(
            "Creating recurring events is not supported yet; omit 'recurrence'"
        )
    if not isinstance(subject, str) or not subject.strip():
        raise InvalidArgumentError("subject must not be empty")

    if all_day:
        start_value = parse_date_only(start, "start")
        end_value = parse_date_only(end, "end")
        if end_value < start_value:
            raise InvalidArgumentError(
                f"Invalid range: end {end!r} is before start {start!r}"
            )
    else:
        start_value = parse_datetime(start, "start", config)
        end_value = parse_datetime(end, "end", config)
        if end_value <= start_value:
            raise InvalidArgumentError(
                f"Invalid range: end {end!r} must be after start {start!r}"
            )

    required = list(attendees or [])
    optional = list(optional_attendees or [])
    has_attendees = bool(required or optional)
    # Без участников слать некому - флаг не влияет.
    will_send = bool(send_invitations and has_attendees)

    item = CalendarItem(
        account=account,
        folder=account.calendar,
        subject=subject.strip(),
        start=start_value,
        end=end_value,
        is_all_day=all_day,
        location=location,
        body=body,
        required_attendees=required or None,
        optional_attendees=optional or None,
    )

    try:
        item.save(
            send_meeting_invitations=(
                SEND_TO_ALL_AND_SAVE_COPY if will_send else SEND_TO_NONE
            )
        )
    except Exception as exc:
        raise translate_ews_error(exc) from exc

    result = format_event_details(item, config.timezone)
    result["invitations_sent"] = will_send
    return result
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_calendar_write.py -v`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор**

Run: `uv run pytest -q`
Expected: PASS, регрессий нет

- [ ] **Step 6: Commit**

```bash
git add src/outlook_mcp/calendar_write.py tests/test_calendar_write.py
git commit -m "feat(outlook-mcp): create_event в сервисном слое"
```

---

## Task 5: `update_event` (сервисный слой)

**Files:**
- Modify: `src/outlook_mcp/calendar_write.py`
- Test: `tests/test_calendar_write.py`

**Interfaces:**
- Consumes: `_fetch_one`, `_find_by_id_in_calendar` (из `calendar_service.py`), `decode_item_id`, `PermissionDeniedError`
- Produces:
  - `update_event(account, config, event_id, *, subject=None, start=None, end=None, location=None, body=None, attendees=None, send_invitations=True, scope="occurrence") -> dict`
  - `_load_item(account, event_id)` и `_ensure_writable(item, config, scope)` — переиспользует Task 6

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_calendar_write.py`:

```python
from outlook_mcp.calendar_write import update_event
from outlook_mcp.errors import ItemNotFoundError, PermissionDeniedError

from .conftest import FakeWriteAccount, utc_dt


def test_update_event_start_only_preserves_duration():
    item = make_writable_event(
        start=utc_dt(2026, 8, 20, 10, 0), end=utc_dt(2026, 8, 20, 11, 0)
    )
    account = FakeWriteAccount(item)
    update_event(account, make_config(), "AAA:CCC", start="2026-08-20T15:00")
    # длительность 1 час сохранена, оба конца сдвинуты
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
        # 09:00 MSK = 06:00 UTC, раньше существующего start
        update_event(account, make_config(), "AAA:CCC", end="2026-08-20T09:00")


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
    assert item.saved_with["send_meeting_invitations"] == "SendToAllAndSaveCopy"
    assert result["invitations_sent"] is True


def test_update_event_send_invitations_false():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    result = update_event(
        account, make_config(), "AAA:CCC", subject="Quiet", send_invitations=False
    )
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


def test_update_event_scope_series_rejected():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    with pytest.raises(InvalidArgumentError):
        update_event(account, make_config(), "AAA:CCC", subject="x", scope="series")


def test_update_event_non_organizer_rejected():
    item = make_writable_event(organizer_email="boss@example.com")
    account = FakeWriteAccount(item)
    with pytest.raises(PermissionDeniedError):
        update_event(account, make_config(), "AAA:CCC", subject="Not mine")


def test_update_event_missing_item_raises_not_found():
    account = FakeWriteAccount(None)
    account.calendar = None
    with pytest.raises(ItemNotFoundError):
        update_event(account, make_config(), "ZZZ:QQQ", subject="Ghost")


def test_update_event_ews_permission_error_translated():
    from exchangelib.errors import ErrorCalendarIsNotOrganizer

    item = make_writable_event(save_error=ErrorCalendarIsNotOrganizer("nope"))
    account = FakeWriteAccount(item)
    with pytest.raises(PermissionDeniedError):
        update_event(account, make_config(), "AAA:CCC", subject="x")
```

`make_config()` должна отдавать конфиг с `ews_email="me@example.com"` — обновить хелпер в начале файла:

```python
def make_config(timezone="Europe/Moscow", email="me@example.com"):
    cfg = Config()
    cfg.timezone = timezone
    cfg.ews_email = email
    return cfg
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_calendar_write.py -k update_event -v`
Expected: FAIL — `ImportError: cannot import name 'update_event'`

- [ ] **Step 3: Реализовать**

Дописать в `src/outlook_mcp/calendar_write.py` (импорты дополнить):

```python
from exchangelib.errors import (
    ErrorAccessDenied,
    ErrorCalendarCannotUpdateDeletedItem,
    ErrorCalendarIsNotOrganizer,
    ErrorCannotDeleteObject,
)

from .calendar_service import _fetch_one, _find_by_id_in_calendar
from .errors import ItemNotFoundError, PermissionDeniedError
from .formatting import decode_item_id
```

```python
# Права на изменение чужой встречи EWS отклоняет по-разному в зависимости от
# операции и настроек ящика; все эти случаи для вызывающего - одно и то же.
_PERMISSION_ERRORS = (
    ErrorCalendarIsNotOrganizer,
    ErrorAccessDenied,
    ErrorCalendarCannotUpdateDeletedItem,
    ErrorCannotDeleteObject,
)

_SERIES_MASTER_TYPE = "RecurringMaster"


def _load_item(account, event_id: str):
    """Fetch a calendar item by event_id, re-resolving a stale ChangeKey."""
    item_id, changekey = decode_item_id(event_id)

    item = None
    stale_changekey = False
    if changekey:
        item, stale_changekey = _fetch_one(account, item_id, changekey)
    if item is None and (stale_changekey or not changekey):
        item = _find_by_id_in_calendar(account, item_id)
    if item is None:
        raise ItemNotFoundError(f"Event with id {event_id!r} was not found")
    return item


def _ensure_writable(item, config: Config, scope: str) -> None:
    """Reject writes we deliberately do not support before touching EWS."""
    if scope != "occurrence":
        raise InvalidArgumentError(
            f"Invalid scope {scope!r}: only 'occurrence' is supported. "
            "Editing a whole recurring series is not supported yet"
        )
    if getattr(item, "type", None) == _SERIES_MASTER_TYPE:
        raise InvalidArgumentError(
            "This event_id refers to a recurring series master. Editing a whole "
            "series is not supported; pass the event_id of a single occurrence "
            "from list_events"
        )

    organizer = getattr(item, "organizer", None)
    organizer_email = (getattr(organizer, "email_address", None) or "").strip().lower()
    own_email = (config.ews_email or "").strip().lower()
    # Если организатор не определяется (нет поля), полагаемся на отказ EWS.
    if organizer_email and own_email and organizer_email != own_email:
        raise PermissionDeniedError(
            "Only the organizer can modify this event "
            f"(organizer is {organizer_email})"
        )


def _translate_write_error(exc: Exception) -> Exception:
    if isinstance(exc, _PERMISSION_ERRORS):
        return PermissionDeniedError(
            "Only the organizer can modify this event"
        )
    return translate_ews_error(exc)


def update_event(
    account,
    config: Config,
    event_id: str,
    *,
    subject: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    body: str | None = None,
    attendees: list[str] | None = None,
    send_invitations: bool = True,
    scope: str = "occurrence",
) -> dict:
    item = _load_item(account, event_id)
    _ensure_writable(item, config, scope)

    if subject is not None:
        if not subject.strip():
            raise InvalidArgumentError("subject must not be empty")
        item.subject = subject.strip()
    if location is not None:
        item.location = location
    if body is not None:
        item.body = body
    if attendees is not None:
        item.required_attendees = list(attendees) or None

    if start is not None or end is not None:
        new_start, new_end = _resolve_new_range(item, start, end, config)
        item.start, item.end = new_start, new_end

    has_attendees = bool(
        getattr(item, "required_attendees", None) or getattr(item, "optional_attendees", None)
    )
    will_send = bool(send_invitations and has_attendees)

    try:
        item.save(
            send_meeting_invitations=(
                SEND_TO_ALL_AND_SAVE_COPY if will_send else SEND_TO_NONE
            )
        )
    except Exception as exc:
        raise _translate_write_error(exc) from exc

    # После обновления occurrence EWS может вернуть новый id - формат ответа
    # строится по item после save, а не по входному event_id.
    result = format_event_details(item, config.timezone)
    result["invitations_sent"] = will_send
    return result


def _resolve_new_range(item, start: str | None, end: str | None, config: Config):
    """Work out the new (start, end) pair from partially supplied values.

    Deliberately asymmetric, matching what the phrasing means: giving only a
    start is "move the meeting" (duration is preserved), giving only an end is
    "make it run until X" (duration changes).
    """
    if start is not None and end is not None:
        new_start = parse_datetime(start, "start", config)
        new_end = parse_datetime(end, "end", config)
    elif start is not None:
        new_start = parse_datetime(start, "start", config)
        duration = item.end - item.start
        new_end = new_start + duration
    else:
        new_start = item.start
        new_end = parse_datetime(end, "end", config)

    if new_end <= new_start:
        raise InvalidArgumentError(
            f"Invalid range: end {new_end.isoformat()} must be after "
            f"start {new_start.isoformat()}"
        )
    return new_start, new_end
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_calendar_write.py -v`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/outlook_mcp/calendar_write.py tests/test_calendar_write.py
git commit -m "feat(outlook-mcp): update_event в сервисном слое"
```

---

## Task 6: `delete_event` (сервисный слой)

**Files:**
- Modify: `src/outlook_mcp/calendar_write.py`
- Test: `tests/test_calendar_write.py`

**Interfaces:**
- Consumes: `_load_item`, `_ensure_writable`, `_translate_write_error` (Task 5)
- Produces: `delete_event(account, config, event_id, *, send_cancellations=True, scope="occurrence") -> dict`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_calendar_write.py`:

```python
from outlook_mcp.calendar_write import delete_event


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


def test_delete_event_scope_series_rejected():
    item = make_writable_event()
    account = FakeWriteAccount(item)
    with pytest.raises(InvalidArgumentError):
        delete_event(account, make_config(), "AAA:CCC", scope="series")


def test_delete_event_missing_item_raises_not_found():
    account = FakeWriteAccount(None)
    account.calendar = None
    with pytest.raises(ItemNotFoundError):
        delete_event(account, make_config(), "ZZZ:QQQ")


def test_delete_event_ews_permission_error_translated():
    from exchangelib.errors import ErrorAccessDenied

    item = make_writable_event(delete_error=ErrorAccessDenied("nope"))
    account = FakeWriteAccount(item)
    with pytest.raises(PermissionDeniedError):
        delete_event(account, make_config(), "AAA:CCC")
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_calendar_write.py -k delete_event -v`
Expected: FAIL — `ImportError: cannot import name 'delete_event'`

- [ ] **Step 3: Реализовать**

Дописать в `src/outlook_mcp/calendar_write.py`:

```python
from exchangelib.items import SEND_MEETING_CANCELLATIONS_CHOICES  # noqa: F401 - документирует набор значений
```

(если линтер против неиспользуемого импорта — не добавлять его, значения ниже те же строки, что и для приглашений)

```python
def delete_event(
    account,
    config: Config,
    event_id: str,
    *,
    send_cancellations: bool = True,
    scope: str = "occurrence",
) -> dict:
    item = _load_item(account, event_id)
    _ensure_writable(item, config, scope)

    subject = getattr(item, "subject", None)
    has_attendees = bool(
        getattr(item, "required_attendees", None) or getattr(item, "optional_attendees", None)
    )
    will_send = bool(send_cancellations and has_attendees)

    try:
        item.delete(
            send_meeting_cancellations=(
                SEND_TO_ALL_AND_SAVE_COPY if will_send else SEND_TO_NONE
            )
        )
    except Exception as exc:
        raise _translate_write_error(exc) from exc

    return {
        "deleted": True,
        "event_id": event_id,
        "subject": subject,
        "cancellations_sent": will_send,
    }
```

**Важно:** тест `test_delete_event_sends_cancellations_by_default` ожидает `cancellations_sent: True` — значит `make_writable_event` должна создавать встречу с участником. Добавить в `tests/conftest.py` в `make_writable_event` параметр и дефолт:

```python
    attendees=None,
```

и в конструктор `RecordingCalendarItem`:

```python
        required_attendees=attendees if attendees is not None else [
            FakeAttendee(FakeMailbox(name="A", email_address="a@example.com"))
        ],
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_calendar_write.py -v`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор**

Run: `uv run pytest -q`
Expected: PASS — в том числе тесты update_event, на которые повлиял дефолт участников

- [ ] **Step 6: Commit**

```bash
git add src/outlook_mcp/calendar_write.py tests/test_calendar_write.py tests/conftest.py
git commit -m "feat(outlook-mcp): delete_event в сервисном слое"
```

---

## Task 7: MCP-tools и условная регистрация

**Files:**
- Modify: `src/outlook_mcp/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `create_event`, `update_event`, `delete_event` (Task 4-6), `Config.allow_write` (Task 1), существующие `_validate_emails`, `get_account`
- Produces: MCP-tools `create_event`, `update_event`, `delete_event`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_server.py`:

```python
import importlib


def _tool_names(module):
    import asyncio

    return {t.name for t in asyncio.run(module.mcp.list_tools())}


def test_write_tools_registered_by_default(monkeypatch):
    monkeypatch.delenv("EWS_ALLOW_WRITE", raising=False)
    module = importlib.reload(server)
    try:
        names = _tool_names(module)
        assert {"create_event", "update_event", "delete_event"} <= names
    finally:
        monkeypatch.delenv("EWS_ALLOW_WRITE", raising=False)
        importlib.reload(server)


def test_write_tools_absent_when_disabled(monkeypatch):
    monkeypatch.setenv("EWS_ALLOW_WRITE", "0")
    module = importlib.reload(server)
    try:
        names = _tool_names(module)
        assert not ({"create_event", "update_event", "delete_event"} & names)
        # читающие tools на месте
        assert "list_events" in names
    finally:
        monkeypatch.delenv("EWS_ALLOW_WRITE", raising=False)
        importlib.reload(server)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_server.py -k write_tools -v`
Expected: FAIL — `create_event` отсутствует в списке tools

- [ ] **Step 3: Реализовать**

В `src/outlook_mcp/server.py` дополнить импорты:

```python
from .calendar_write import (
    create_event as create_event_svc,
    delete_event as delete_event_svc,
    update_event as update_event_svc,
)
```

В конец файла, перед `def main()`:

```python
# Пишущие tools регистрируются только при EWS_ALLOW_WRITE != 0/false/no.
# Именно не регистрируются, а не "отвечают отказом": LLM не должен видеть
# инструмент, которым нельзя воспользоваться, иначе он будет пытаться его звать.
if _config.allow_write:

    @mcp.tool()
    def create_event(
        subject: str,
        start: str,
        end: str,
        attendees: list[str] | None = None,
        optional_attendees: list[str] | None = None,
        location: str | None = None,
        body: str | None = None,
        all_day: bool = False,
        send_invitations: bool = True,
    ) -> dict:
        """Create a calendar event.

        start/end - ISO datetimes. Without a UTC offset ("2026-08-20T15:00")
        the time is read in the mailbox's local timezone, which is what a user
        means by "at 15:00". With an offset it is respected as given.

        attendees/optional_attendees - SMTP addresses ONLY, never names. If you
        only know a person's name, call resolve_person first; if it returns more
        than one candidate, ask the user which one instead of picking yourself -
        inviting the wrong person cannot be undone.

        all_day - for whole-day events (vacation, offsite). start/end are then
        read as dates, the time part is ignored, and end is INCLUSIVE:
        "20th to 22nd" means all three days.

        send_invitations - when true (default) and there are attendees, they
        receive an invitation, matching what creating a meeting in Outlook does.
        Set to false to put the event in your own calendar only. With no
        attendees nothing is sent either way.

        Recurring events are not supported; create a single event instead.
        The result mirrors get_event and adds "invitations_sent".
        """
        try:
            required = _validate_emails(attendees)
            optional = _validate_emails(optional_attendees)
            account = get_account()
            result = create_event_svc(
                account, _config,
                subject=subject, start=start, end=end,
                attendees=required, optional_attendees=optional,
                location=location, body=body, all_day=all_day,
                send_invitations=send_invitations,
            )
            logger.info(
                "create_event created event, invitations_sent=%s",
                result["invitations_sent"],
            )
            return result
        except OutlookMcpError as exc:
            logger.error("create_event failed: %s", exc.code)
            return exc.to_dict()

    @mcp.tool()
    def update_event(
        event_id: str,
        subject: str | None = None,
        start: str | None = None,
        end: str | None = None,
        location: str | None = None,
        body: str | None = None,
        attendees: list[str] | None = None,
        send_invitations: bool = True,
    ) -> dict:
        """Update an existing calendar event. Only the organizer can do this.

        Pass only the fields you want to change; anything left out stays as it
        is. Fields cannot be cleared - passing an empty subject is an error.

        Timing: to MOVE the meeting pass start (the duration is preserved and
        end moves with it). To CHANGE ITS LENGTH pass end (start stays put). To
        set both boundaries pass both.

        For a recurring meeting, pass the event_id of the specific occurrence
        from list_events - this updates that occurrence only. The id of the
        series itself is rejected; editing a whole series is not supported.

        send_invitations - when true (default) attendees are notified of the
        change, which matters most for a move: a rescheduled meeting nobody was
        told about breaks other people's day.
        """
        try:
            participants = _validate_emails(attendees) if attendees is not None else None
            account = get_account()
            result = update_event_svc(
                account, _config, event_id,
                subject=subject, start=start, end=end,
                location=location, body=body, attendees=participants,
                send_invitations=send_invitations,
            )
            logger.info(
                "update_event succeeded, invitations_sent=%s", result["invitations_sent"]
            )
            return result
        except OutlookMcpError as exc:
            logger.error("update_event failed: %s", exc.code)
            return exc.to_dict()

    @mcp.tool()
    def delete_event(event_id: str, send_cancellations: bool = True) -> dict:
        """Delete (cancel) a calendar event. Only the organizer can do this.

        send_cancellations - when true (default) attendees are notified, which
        is what cancelling a meeting in Outlook does; otherwise the meeting
        would silently stay in their calendars.

        For a recurring meeting, pass the event_id of a specific occurrence
        from list_events to cancel that one occurrence. The id of the series
        itself is rejected.

        Declining someone else's meeting is a different operation and is not
        supported - this tool only cancels meetings you organize.
        """
        try:
            account = get_account()
            result = delete_event_svc(
                account, _config, event_id, send_cancellations=send_cancellations
            )
            logger.info(
                "delete_event succeeded, cancellations_sent=%s",
                result["cancellations_sent"],
            )
            return result
        except OutlookMcpError as exc:
            logger.error("delete_event failed: %s", exc.code)
            return exc.to_dict()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_server.py -v`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Проверить, что сервер стартует и отдаёт tools без кредов**

Run: `uv run ews-mcp-server --help`
Expected: печатает справку, не зависает

- [ ] **Step 7: Commit**

```bash
git add src/outlook_mcp/server.py tests/test_server.py
git commit -m "feat(outlook-mcp): MCP-tools create/update/delete_event"
```

---

## Task 8: Документация

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `install/README.md`, `src/outlook_mcp/server.py:283-286`

- [ ] **Step 1: Обновить текст `--help`**

В `server.py`, в `main()`, заменить первую строку справки:

```python
            "outlook-mcp — MCP server for Exchange/EWS (stdio transport).\n"
            "Reads calendar and mail; creates/updates/deletes calendar events.\n"
            "Run without arguments to start the MCP stdio server.\n"
            "Required env: EWS_URL, EWS_USERNAME, EWS_EMAIL, EWS_PASSWORD.\n"
            "Optional: EWS_ALLOW_WRITE=0 disables the calendar-write tools."
```

- [ ] **Step 2: Обновить README**

Раздел «Не делать» — заменить первую строку:

```markdown
- Никакой записи в почту — почта только на чтение.
- Запись в календарь — только через явные tools (`create_event`,
  `update_event`, `delete_event`), выключается `EWS_ALLOW_WRITE=0`.
```

В описание tools добавить три новых с их параметрами (по docstring из Task 7).

- [ ] **Step 3: Обновить CLAUDE.md**

- В шапке: убрать «только для чтения», описать, что календарь пишущий.
- В «Структура» добавить пункт про `calendar_write.py`.
- В «Важные решения» добавить (кратко, со ссылкой на спек):
  - `EWS_ALLOW_WRITE` и почему tools не регистрируются, а не отказывают;
  - дефолт `send_invitations=True` и почему (тихий разрыв ожиданий хуже ошибочного приглашения);
  - несимметричность start/end в `update_event`;
  - all-day: включительный `end`, `EWSDate` вместо `EWSDateTime`;
  - `ErrorCannotUpdateObject` **не существует** в exchangelib 5.6.0 — актуальный список в `_PERMISSION_ERRORS`;
  - после `save()` обновления occurrence id item'а может измениться — ответ строится по item после save.

- [ ] **Step 4: Обновить install/README.md**

Добавить описание необязательной переменной `EWS_ALLOW_WRITE` (по умолчанию запись включена; `0` — read-only сервер). Сами скрипты не менять.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md install/README.md src/outlook_mcp/server.py
git commit -m "docs(outlook-mcp): запись в календарь в документации"
```

---

## Task 9: Ручная проверка на живом Exchange

**Обязательна.** История проекта прямая: `WorkingPeriod.weekdays`, путь `sender`, наивное время от `GetUserAvailability`, `list_events` с сериями — всё прошло юнит-тесты и сломалось только вживую. Для записи цена ошибки выше: страдает чужой календарь.

Требуется VPN и рабочие креды в `~/.config/outlook-mcp/.env`.

Все шаги, кроме 5, — на встречах **без участников**.

- [ ] **Step 1: create_event без участников**

Создать встречу на завтра 15:00–16:00, тема «MCP write test 1».
Проверить в Outlook: дата, время (именно 15:00 по местному, не сдвиг), тема.

- [ ] **Step 2: update_event — перенос**

`update_event(event_id, start="<завтра>T17:00")`.
Ожидается: встреча 17:00–18:00, длительность сохранена.

- [ ] **Step 3: update_event — изменение длительности**

`update_event(event_id, end="<завтра>T19:00")`.
Ожидается: 17:00–19:00, начало не поехало.

- [ ] **Step 4: all-day на 2 дня**

`create_event(subject="MCP write test all-day", start="<дата>", end="<дата+1>", all_day=True)`.
Ожидается: плашка над днями (не полоса поперёк дня), включены **оба** дня.
**Записать в CLAUDE.md фактическое поведение включительного end** — это единственное место, где вход не равен тому, что уходит в EWS.

- [ ] **Step 5: delete_event**

Удалить обе тестовые встречи, проверить, что исчезли из Outlook.

- [ ] **Step 6: полный цикл с участником**

Участник — свой второй адрес или заранее согласившийся коллега.
1. `create_event(..., attendees=["<адрес>"])` → приглашение дошло;
2. `update_event(event_id, start=...)` → обновление дошло;
3. `delete_event(event_id)` → отмена дошла.

- [ ] **Step 7: экземпляр серии**

Взять `event_id` одного экземпляра регулярной встречи из `list_events`,
`update_event` с новым временем.
Ожидается: изменился один экземпляр, серия цела.
**Проверить, что `event_id` в ответе рабочий** — сделать по нему `get_event`
(id occurrence меняется после save).

- [ ] **Step 8: серия целиком отклоняется**

`update_event` с `event_id` мастера серии (item_type `RecurringMaster` в `list_events`).
Ожидается: понятная ошибка `invalid_argument`, не EWS-код.

- [ ] **Step 9: чужая встреча**

`update_event` на встрече, где организатор — другой человек.
Ожидается: `permission_denied` с внятным текстом, не сырой EWS-код.

- [ ] **Step 10: зафиксировать находки**

Всё, что разошлось с ожиданиями, — в CLAUDE.md, раздел про запись.
Если поведение потребовало правки кода — отдельный коммит с тестом,
воспроизводящим найденное.

- [ ] **Step 11: финальная проверка и commit**

```bash
uv run pytest -q
git add -A && git commit -m "docs(outlook-mcp): результаты живой проверки записи в календарь"
```

---

## Self-Review

**Покрытие спека:**

| Требование спека | Задача |
|---|---|
| `EWS_ALLOW_WRITE`, дефолт включён | Task 1 |
| tools не регистрируются при выключенном | Task 7 |
| `PermissionDeniedError` | Task 2 |
| naive-время в таймзоне конфига, offset уважается | Task 3 |
| `create_event` + all-day (включительный end) | Task 4 |
| `send_invitations` дефолт True, нет участников → SendToNone | Task 4, 5 |
| участники только email | Task 4 (`_validate_emails` в Task 7) |
| `recurrence` шов (только None) | Task 4 |
| `update_event`, `None` = не менять | Task 5 |
| несимметричность start/end | Task 5 (`_resolve_new_range`) |
| проверка организатора | Task 5 (`_ensure_writable`) |
| `RecurringMaster` отклоняется, `Occurrence` разрешён | Task 5, 6 |
| `scope` шов (только "occurrence") | Task 5, 6 |
| `delete_event` + `cancellations_sent` | Task 6 |
| отдельный модуль `calendar_write.py` | Task 3-6 |
| переиспользование `_fetch_one` | Task 5 |
| фейки повторяют контракт exchangelib | Task 3 |
| документация | Task 8 |
| ручная проверка на живом Exchange | Task 9 |

**Расхождения со спеком, исправленные в плане:**

1. Спек называет `ErrorCannotUpdateObject` — **такого класса в exchangelib 5.6.0 нет**. Реальные: `ErrorCalendarIsNotOrganizer`, `ErrorAccessDenied`, `ErrorCalendarCannotUpdateDeletedItem`, `ErrorCannotDeleteObject`.
2. Спек говорит «`_validate_emails` из `server.py`» — функция остаётся в `server.py`, tools валидируют адреса до вызова сервиса (перенос не нужен).
3. Добавлено требование, которого не было в спеке: после `save()` обновления occurrence id item'а меняется, поэтому ответ строится по item после save.

**Согласованность имён:** `create_event`/`update_event`/`delete_event` — имена и в сервисе, и в tools; в `server.py` сервисные импортируются как `*_svc` (существующий паттерн файла). `_load_item`, `_ensure_writable`, `_translate_write_error`, `_resolve_new_range` определены в Task 5, используются в Task 5-6. `make_config()` определяется в Task 3 и расширяется в Task 5 — расширение прописано явно.
