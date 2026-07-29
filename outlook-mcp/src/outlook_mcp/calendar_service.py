import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from exchangelib import EWSTimeZone
from exchangelib.errors import ErrorInvalidChangeKey, ErrorInvalidIdMalformed, ErrorItemNotFound

from .config import Config
from .errors import InvalidArgumentError, ItemNotFoundError, OutlookMcpError
from .ews_client import translate_ews_error
from .formatting import decode_item_id, format_event_details, format_event_summary

logger = logging.getLogger(__name__)

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

# EWS busy_type classification (exchangelib.fields.FREE_BUSY_CHOICES):
# Busy/OOF block a slot outright. Tentative blocks it too, but is reported
# separately in "tentative_slots" so the caller can offer it with a caveat.
# Free/WorkingElsewhere/NoData never block (handled by omission below).
_HARD_BUSY_TYPES = frozenset({"Busy", "OOF"})
_TENTATIVE_BUSY_TYPE = "Tentative"
_DEFAULT_BUSY_TYPE = "Busy"  # mirrors exchangelib's FreeBusyStatusField(default="Busy")


def find_free_slots(
    account,
    target_date: date,
    duration_min: int,
    config: Config,
    emails: list[str] | None = None,
    include_self: bool = True,
    debug: bool = False,
) -> dict:
    if not include_self and not emails:
        raise InvalidArgumentError(
            "include_self=false requires at least one email in 'emails': "
            "with no participants there is nobody whose calendar could be checked"
        )

    tz = ZoneInfo(config.timezone)
    ews_tz = EWSTimeZone.from_zoneinfo(tz)
    day_start = datetime.combine(target_date, time.min, tzinfo=ews_tz)
    day_end = datetime.combine(target_date, time.max, tzinfo=ews_tz)

    try:
        own_view = _get_free_busy_view(account, day_start, day_end)
    except Exception as exc:
        raise translate_ews_error(exc) from exc

    # Own view is fetched (and its failure stays fatal) regardless of
    # include_self: it's the source of the working-hours window even when
    # its busy time is excluded from the intersection.
    labeled_views: list[tuple[str, object]] = [(f"self ({getattr(account, 'primary_smtp_address', 'self')})", own_view)]

    working_period = _working_period_for_weekday(own_view, target_date.strftime("%A"))
    if working_period is None:
        result = {
            "slots": [],
            "tentative_slots": [],
            "has_more": False,
            "unavailable": [],
            "reason": "no_working_hours_for_weekday",
        }
        if debug:
            result["diagnostics"] = _build_diagnostics(
                tz=tz,
                day_start=day_start,
                day_end=day_end,
                target_date=target_date,
                labeled_views=labeled_views,
                counted_in_busy={labeled_views[0][0]: False},
                working_period=None,
                duration_min=duration_min,
                include_self=include_self,
                hard_busy=[],
                tentative_busy=[],
                free_windows=[],
                slot_grid_count=0,
            )
        return result

    work_start = datetime.combine(target_date, working_period[0], tzinfo=tz)
    work_end = datetime.combine(target_date, working_period[1], tzinfo=tz)

    counted_in_busy = {labeled_views[0][0]: include_self}
    unavailable: list[dict] = []
    if emails:
        other_views, unavailable = _collect_participant_views(account, emails, day_start, day_end)
        for email, view in other_views:
            labeled_views.append((email, view))
            counted_in_busy[email] = True

    busy_views = [view for label, view in labeled_views if counted_in_busy.get(label)]

    hard_busy = _merge_intervals(
        [
            interval
            for view in busy_views
            for interval in _busy_intervals_within(view, work_start, work_end, tz, _HARD_BUSY_TYPES)
        ]
    )
    tentative_busy = _merge_intervals(
        [
            interval
            for view in busy_views
            for interval in _busy_intervals_within(
                view, work_start, work_end, tz, frozenset({_TENTATIVE_BUSY_TYPE})
            )
        ]
    )

    # Slot grid is derived from hard-busy only. Subtracting tentative
    # intervals in a second pass would shift _iter_slot_starts' anchor
    # (it starts counting from each free window's own start), falsely
    # marking genuinely-free slots as tentative just because an earlier
    # tentative block shifted the grid. Instead, generate the grid once
    # and classify each slot by whether it overlaps a tentative interval.
    free_windows = _subtract_intervals(work_start, work_end, hard_busy)
    duration = timedelta(minutes=duration_min)
    slots: list[dict] = []
    tentative_slots: list[dict] = []
    for window_start, window_end in free_windows:
        for start in _iter_slot_starts(window_start, window_end, duration):
            end = start + duration
            entry = {"start": start.isoformat(), "end": end.isoformat()}
            if _overlaps_any(start, end, tentative_busy):
                tentative_slots.append(entry)
            else:
                slots.append(entry)

    reason = _compute_reason(
        slots=slots,
        tentative_slots=tentative_slots,
        free_windows=free_windows,
        emails=emails,
        unavailable=unavailable,
        include_self=include_self,
    )

    result = {
        "slots": slots,
        "tentative_slots": tentative_slots,
        "has_more": False,
        "unavailable": unavailable,
        "reason": reason,
    }
    if debug:
        result["diagnostics"] = _build_diagnostics(
            tz=tz,
            day_start=day_start,
            day_end=day_end,
            target_date=target_date,
            labeled_views=labeled_views,
            counted_in_busy=counted_in_busy,
            working_period=working_period,
            duration_min=duration_min,
            include_self=include_self,
            hard_busy=hard_busy,
            tentative_busy=tentative_busy,
            free_windows=free_windows,
            slot_grid_count=len(slots) + len(tentative_slots),
            work_start=work_start,
            work_end=work_end,
        )
        result["diagnostics"]["unavailable"] = unavailable
    return result


def _compute_reason(
    *, slots: list, tentative_slots: list, free_windows: list, emails, unavailable: list, include_self: bool
) -> str:
    # Checked before "ok": if include_self=False and every participant's
    # free/busy failed, nobody's calendar was actually checked - a non-empty
    # "slots" here would be a false positive (the whole working day, unfiltered
    # by anyone's busy time), not a genuine answer.
    if emails and not include_self and len(unavailable) == len(emails):
        return "all_participants_unavailable"
    if slots:
        return "ok"
    if tentative_slots:
        return "only_tentative"
    if free_windows:
        return "no_window_fits_duration"
    return "fully_busy"


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


def _get_free_busy_view_for_email(account, email: str, window_start: datetime, window_end: datetime):
    """Fetch a FreeBusyView for a mailbox we do not own, by SMTP address.

    exchangelib's get_free_busy_info accepts either an Account or a plain str
    as the first tuple element (protocol.py: `account.primary_smtp_address if
    isinstance(account, Account) else account`), so a string is the supported
    way to request a foreign mailbox.
    """
    results = list(
        account.protocol.get_free_busy_info(
            accounts=[(email, "Required", False)],
            start=window_start,
            end=window_end,
        )
    )
    if not results:
        return None
    result = results[0]
    if isinstance(result, Exception):
        raise result
    return result


def _unavailable_reason(exc: Exception) -> str:
    translated = translate_ews_error(exc)
    if isinstance(translated, OutlookMcpError):
        return str(translated)
    return f"{type(exc).__name__}: {exc}"


def _collect_participant_views(
    account,
    emails: list[str],
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[tuple[str, object]], list[dict]]:
    """Fetch a FreeBusyView per email, isolating failures per participant.

    One EWS request per email is deliberate: exchangelib only returns
    per-entry Exception objects for errors listed in
    EWSService.ERRORS_TO_CATCH_IN_RESPONSE (e.g. ErrorMailRecipientNotFound);
    everything else (ErrorNoFreeBusyAccess, ErrorProxyRequestNotAllowed,
    ErrorMailboxMoved, ...) is raised and would abort a single batched call
    for every participant. Do not "optimize" this back into one request.

    Returns (email, view) pairs rather than bare views so diagnostics can
    attribute each view back to its participant.
    """
    views: list[tuple[str, object]] = []
    unavailable: list[dict] = []
    for email in emails:
        try:
            view = _get_free_busy_view_for_email(account, email, window_start, window_end)
        except Exception as exc:  # noqa: BLE001 - deliberately non-fatal per participant
            logger.warning("free/busy unavailable for %s: %s", email, exc)
            unavailable.append({"email": email, "reason": _unavailable_reason(exc)})
            continue
        if view is None:
            unavailable.append({"email": email, "reason": "No free/busy data returned for this mailbox"})
            continue
        logger.info(
            "free/busy view for %s: view_type=%s events=%d merged_len=%d",
            email,
            getattr(view, "view_type", None),
            len(getattr(view, "calendar_events", None) or []),
            len(getattr(view, "merged", None) or ""),
        )
        views.append((email, view))
    return views, unavailable


def _merge_intervals(intervals: list) -> list:
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _weekday_names(weekdays) -> set[str]:
    """Normalize EWS weekdays to day names.

    exchangelib returns WorkingPeriod.weekdays as 1-based indices into
    WEEKDAY_NAMES, not as strings (EnumListField.from_xml in
    exchangelib/fields.py: `[self.enum.index(v) + 1 for v in val.split(" ")]`).
    Strings are still accepted so a future/alternative parsing path or a
    hand-built object keeps working.
    """
    names = set()
    for value in weekdays or []:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if 1 <= value <= len(_WEEKDAY_NAMES):
                names.add(_WEEKDAY_NAMES[value - 1])
        else:
            names.add(str(value))
    return names


def _working_period_for_weekday(view, weekday_name: str):
    """Find the WorkingPeriod covering the given weekday.

    Comparing weekday_name directly against period.weekdays silently never
    matches, because those are integer indices - that made every day look
    like a non-working day and find_free_slots always return no slots.
    """
    if view is None:
        return None
    for period in getattr(view, "working_hours", None) or []:
        if weekday_name in _weekday_names(getattr(period, "weekdays", None)):
            return period.start, period.end
    return None


def _busy_intervals_within(
    view, window_start: datetime, window_end: datetime, tz: ZoneInfo, busy_types: frozenset[str]
):
    intervals = []
    for calendar_event in getattr(view, "calendar_events", None) or []:
        busy_type = getattr(calendar_event, "busy_type", None) or _DEFAULT_BUSY_TYPE
        if busy_type not in busy_types:
            continue
        start = calendar_event.start
        end = calendar_event.end
        # GetUserAvailability's fake account (services/get_user_availability.py:
        # _elem_to_obj) sets default_timezone=<the tz we requested>, so a naive
        # datetime here is already wall-clock time in that tz, not UTC. Tagging
        # it UTC would shift it by the zone offset.
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz)
        start = start.astimezone(tz)
        end = end.astimezone(tz)
        if end > window_start and start < window_end:
            intervals.append((max(start, window_start), min(end, window_end)))
    return sorted(intervals)


def _overlaps_any(start: datetime, end: datetime, intervals: list) -> bool:
    """True if [start, end) intersects any interval in the (merged) list.

    Strict inequalities: an interval that only touches the slot's boundary
    does not count as an overlap.
    """
    return any(interval_start < end and start < interval_end for interval_start, interval_end in intervals)


_MERGED_LEGEND = "0=Free 1=Tentative 2=Busy 3=OOF 4=NoData 5=WorkingElsewhere"
_RAW_EVENTS_CAP = 100


def _format_interval(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


def _describe_intervals(intervals: list) -> list[str]:
    return [_format_interval(start, end) for start, end in intervals]


def _view_diagnostics(
    label: str,
    view,
    tz: ZoneInfo,
    counted_in_busy: bool,
    work_start: datetime | None,
    work_end: datetime | None,
) -> dict:
    """Build a debug-only snapshot of one participant's FreeBusyView.

    Never raises: every field is read defensively so a malformed/unexpected
    view degrades the diagnostics, not the actual find_free_slots answer.
    """
    try:
        view_present = view is not None
        calendar_events = getattr(view, "calendar_events", None) or []
        merged = getattr(view, "merged", None)

        raw_events = []
        histogram: dict[str, int] = {}
        for calendar_event in calendar_events[:_RAW_EVENTS_CAP]:
            busy_type = getattr(calendar_event, "busy_type", None) or _DEFAULT_BUSY_TYPE
            histogram[busy_type] = histogram.get(busy_type, 0) + 1
            start = calendar_event.start
            end = calendar_event.end
            raw_start_repr = repr(start)
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            if end.tzinfo is None:
                end = end.replace(tzinfo=tz)
            raw_events.append(
                {
                    "start": start.astimezone(tz).isoformat(),
                    "end": end.astimezone(tz).isoformat(),
                    "busy_type": busy_type,
                    "raw_start_repr": raw_start_repr,
                }
            )

        hard_after_filter: list[str] = []
        tentative_after_filter: list[str] = []
        if work_start is not None and work_end is not None:
            hard_after_filter = _describe_intervals(
                _busy_intervals_within(view, work_start, work_end, tz, _HARD_BUSY_TYPES)
            )
            tentative_after_filter = _describe_intervals(
                _busy_intervals_within(view, work_start, work_end, tz, frozenset({_TENTATIVE_BUSY_TYPE}))
            )

        return {
            "label": label,
            "counted_in_busy": counted_in_busy,
            "view_present": view_present,
            "view_type": getattr(view, "view_type", None),
            "working_hours_present": bool(getattr(view, "working_hours", None)),
            "calendar_events_count": len(calendar_events),
            "merged_present": bool(merged),
            "merged_length": len(merged) if merged else 0,
            "merged_sample": (merged or "")[:96],
            "merged_legend": _MERGED_LEGEND,
            "raw_events": raw_events,
            "raw_events_truncated": len(calendar_events) > _RAW_EVENTS_CAP,
            "busy_type_histogram": histogram,
            "hard_busy_after_filter": hard_after_filter,
            "tentative_after_filter": tentative_after_filter,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must never break the real answer
        return {"label": label, "diagnostics_error": f"{type(exc).__name__}: {exc}"}


def _build_diagnostics(
    tz: ZoneInfo,
    day_start: datetime,
    day_end: datetime,
    target_date: date,
    labeled_views: list[tuple[str, object]],
    counted_in_busy: dict[str, bool],
    working_period,
    duration_min: int,
    include_self: bool,
    hard_busy: list,
    tentative_busy: list,
    free_windows: list,
    slot_grid_count: int,
    work_start: datetime | None = None,
    work_end: datetime | None = None,
) -> dict:
    own_label = labeled_views[0][0] if labeled_views else None
    return {
        "timezone": str(tz),
        "requested_window": {"start": day_start.isoformat(), "end": day_end.isoformat()},
        "weekday": target_date.strftime("%A"),
        "working_hours_source": own_label,
        "working_window": (
            {"start": working_period[0].strftime("%H:%M"), "end": working_period[1].strftime("%H:%M")}
            if working_period
            else None
        ),
        "duration_min": duration_min,
        "include_self": include_self,
        "views": [
            _view_diagnostics(label, view, tz, counted_in_busy.get(label, False), work_start, work_end)
            for label, view in labeled_views
        ],
        "merged_hard_busy": _describe_intervals(hard_busy),
        "merged_tentative": _describe_intervals(tentative_busy),
        "free_windows": _describe_intervals(free_windows),
        "slot_grid_count": slot_grid_count,
    }


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