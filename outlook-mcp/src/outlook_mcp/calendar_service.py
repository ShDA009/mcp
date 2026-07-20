from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from exchangelib import EWSTimeZone
from exchangelib.errors import ErrorInvalidChangeKey, ErrorInvalidIdMalformed, ErrorItemNotFound

from .config import Config
from .errors import ItemNotFoundError
from .ews_client import translate_ews_error
from .formatting import decode_item_id, format_event_details, format_event_summary

_STALE_ID_ERRORS = (ErrorInvalidChangeKey, ErrorItemNotFound, ErrorInvalidIdMalformed)


def list_events_for_range(
    account,
    start_date: date,
    end_date: date,
    config: Config,
    limit: int | None = None,
) -> dict:
    tz = ZoneInfo(config.timezone)
    start_dt = datetime.combine(start_date, time.min, tzinfo=tz)
    end_dt = datetime.combine(end_date, time.max, tzinfo=tz)
    effective_limit = limit if limit is not None else config.default_limit
    effective_limit = min(effective_limit, config.max_limit)

    try:
        qs = account.calendar.view(start=start_dt, end=end_dt, max_items=effective_limit + 1)
        items = list(qs)
    except Exception as exc:
        raise translate_ews_error(exc) from exc

    has_more = len(items) > effective_limit
    events = [format_event_summary(item, config.timezone) for item in items[:effective_limit]]
    return {"events": events, "has_more": has_more}


def get_event_by_id(account, event_id: str, config: Config) -> dict:
    item_id, changekey = decode_item_id(event_id)

    item = None
    stale_changekey = False
    if changekey:
        item, stale_changekey = _fetch_one(account, item_id, changekey)

    if item is None and (stale_changekey or not changekey):
        # ChangeKey may be stale (item was modified after the ID was issued),
        # or no changekey was supplied at all. Re-resolve by scanning the calendar.
        item = _find_by_id_in_calendar(account, item_id)

    if item is None:
        raise ItemNotFoundError(f"Event with id {event_id!r} was not found")

    return format_event_details(item, config.timezone)


def _fetch_one(account, item_id: str, changekey: str):
    try:
        results = list(account.fetch(ids=[(item_id, changekey)]))
    except _STALE_ID_ERRORS:
        return None, True
    except Exception as exc:
        raise translate_ews_error(exc) from exc

    if not results:
        return None, True
    result = results[0]
    if isinstance(result, _STALE_ID_ERRORS):
        return None, True
    if isinstance(result, Exception):
        raise translate_ews_error(result)
    return result, False


_ID_RESOLUTION_WINDOW_DAYS = 180


def _find_by_id_in_calendar(account, item_id: str):
    tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    window_start = now - timedelta(days=_ID_RESOLUTION_WINDOW_DAYS)
    window_end = now + timedelta(days=_ID_RESOLUTION_WINDOW_DAYS)
    try:
        qs = account.calendar.filter(start__lt=window_end, end__gt=window_start)
        for item in qs:
            if item.id == item_id:
                return item
    except Exception as exc:
        raise translate_ews_error(exc) from exc
    return None


_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def find_free_slots(account, target_date: date, duration_min: int, config: Config) -> dict:
    tz = ZoneInfo(config.timezone)
    ews_tz = EWSTimeZone.from_zoneinfo(tz)
    day_start = datetime.combine(target_date, time.min, tzinfo=ews_tz)
    day_end = datetime.combine(target_date, time.max, tzinfo=ews_tz)

    try:
        view = _get_free_busy_view(account, day_start, day_end)
    except Exception as exc:
        raise translate_ews_error(exc) from exc

    working_period = _working_period_for_weekday(view, target_date.strftime("%A"))
    if working_period is None:
        return {"slots": [], "has_more": False}

    work_start = datetime.combine(target_date, working_period[0], tzinfo=tz)
    work_end = datetime.combine(target_date, working_period[1], tzinfo=tz)

    busy_intervals = _busy_intervals_within(view, work_start, work_end, tz)
    free_slots = _subtract_intervals(work_start, work_end, busy_intervals)

    duration = timedelta(minutes=duration_min)
    slots = [
        {"start": start.isoformat(), "end": (start + duration).isoformat()}
        for window_slot_start, window_slot_end in free_slots
        for start in _iter_slot_starts(window_slot_start, window_slot_end, duration)
    ]
    return {"slots": slots, "has_more": False}


def _get_free_busy_view(account, window_start: datetime, window_end: datetime):
    results = account.protocol.get_free_busy_info(
        accounts=[(account, "Organizer", False)],
        start=window_start,
        end=window_end,
    )
    results = list(results)
    if not results:
        return None
    result = results[0]
    if isinstance(result, Exception):
        raise result
    return result


def _working_period_for_weekday(view, weekday_name: str):
    if view is None:
        return None
    for period in getattr(view, "working_hours", None) or []:
        if weekday_name in (period.weekdays or []):
            return period.start, period.end
    return None


def _busy_intervals_within(view, window_start: datetime, window_end: datetime, tz: ZoneInfo):
    intervals = []
    for calendar_event in getattr(view, "calendar_events", None) or []:
        start = calendar_event.start
        end = calendar_event.end
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo("UTC"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=ZoneInfo("UTC"))
        start = start.astimezone(tz)
        end = end.astimezone(tz)
        if end > window_start and start < window_end:
            intervals.append((max(start, window_start), min(end, window_end)))
    return sorted(intervals)


def _subtract_intervals(window_start: datetime, window_end: datetime, busy: list) -> list:
    free = []
    cursor = window_start
    for busy_start, busy_end in busy:
        if busy_start > cursor:
            free.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if cursor < window_end:
        free.append((cursor, window_end))
    return free


def _iter_slot_starts(slot_start: datetime, slot_end: datetime, duration: timedelta):
    cursor = slot_start
    while cursor + duration <= slot_end:
        yield cursor
        cursor += duration