import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from exchangelib import CalendarItem, EWSDate, EWSDateTime, EWSTimeZone
from exchangelib.errors import (
    ErrorAccessDenied,
    ErrorCalendarCannotUpdateDeletedItem,
    ErrorCalendarIsNotOrganizer,
    ErrorCannotDeleteObject,
)
from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY, SEND_TO_NONE

from .calendar_service import _fetch_one, _find_by_id_in_calendar
from .config import Config
from .errors import InvalidArgumentError, ItemNotFoundError, PermissionDeniedError
from .ews_client import translate_ews_error
from .formatting import decode_item_id, format_event_details

logger = logging.getLogger(__name__)

_DATETIME_HINT = "expected an ISO datetime like 2026-08-20T15:00"
_DATE_HINT = "expected a date like 2026-08-20"

# EWS refuses a write on somebody else's meeting through several different
# errors depending on the operation and mailbox settings; to the caller they
# all mean the same thing.
_PERMISSION_ERRORS = (
    ErrorCalendarIsNotOrganizer,
    ErrorAccessDenied,
    ErrorCalendarCannotUpdateDeletedItem,
    ErrorCannotDeleteObject,
)

_SERIES_MASTER_TYPE = "RecurringMaster"
_OCCURRENCE_SCOPE = "occurrence"


def _parse_iso(value: str, field_name: str, hint: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise InvalidArgumentError(f"Invalid {field_name} {value!r}, {hint}")
    try:
        # "Z" is valid ISO-8601 but datetime.fromisoformat only learned it in
        # 3.11; normalising keeps the accepted syntax stable across versions.
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidArgumentError(f"Invalid {field_name} {value!r}, {hint}") from exc


def parse_datetime(value: str, field_name: str, config: Config) -> EWSDateTime:
    """Parse an ISO-8601 string into an EWSDateTime.

    A naive string ("2026-08-20T15:00") is read as wall-clock time in the
    configured timezone - that is what a user means by "at 15:00", and making
    the caller guess a UTC offset would silently shift the meeting by hours.
    A string carrying an offset is respected as-is.
    """
    parsed = _parse_iso(value, field_name, _DATETIME_HINT)

    # A bare date carries no time of day. Rejecting it here (rather than
    # silently booking midnight) is what makes all_day=True discoverable.
    if "T" not in value and " " not in value.strip():
        raise InvalidArgumentError(
            f"Invalid {field_name} {value!r}: no time of day. "
            "Pass a time (2026-08-20T15:00) or set all_day=true"
        )

    tz = EWSTimeZone.from_zoneinfo(ZoneInfo(config.timezone))
    if parsed.tzinfo is None:
        # .replace() keeps a plain datetime, so it needs wrapping; .astimezone()
        # on an EWSTimeZone already returns an EWSDateTime, and from_datetime()
        # rejects its own type - hence the two different paths.
        return EWSDateTime.from_datetime(parsed.replace(tzinfo=tz))
    return parsed.astimezone(tz)


def parse_date_only(value: str, field_name: str) -> EWSDate:
    """Parse the calendar day out of an ISO string, ignoring any time part."""
    parsed = _parse_iso(value, field_name, _DATE_HINT)
    return EWSDate(parsed.year, parsed.month, parsed.day)


def _resolve_range(start: str, end: str, all_day: bool, config: Config):
    """Parse and sanity-check the start/end pair for a new event."""
    if all_day:
        start_value = parse_date_only(start, "start")
        end_value = parse_date_only(end, "end")
        # end is the last day of the event, not the day after it: "20th to
        # 22nd" means all three days, so a single-day event has start == end.
        if end_value < start_value:
            raise InvalidArgumentError(
                f"Invalid range: end {end!r} is before start {start!r}"
            )
        return start_value, end_value

    start_value = parse_datetime(start, "start", config)
    end_value = parse_datetime(end, "end", config)
    if end_value <= start_value:
        raise InvalidArgumentError(
            f"Invalid range: end {end!r} must be after start {start!r}"
        )
    return start_value, end_value


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

    start_value, end_value = _resolve_range(start, end, all_day, config)

    required = list(attendees or [])
    optional = list(optional_attendees or [])
    # With nobody to notify the flag is moot - never claim invitations were sent.
    will_send = bool(send_invitations and (required or optional))

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
    """Reject writes we deliberately do not support, before touching EWS."""
    if scope != _OCCURRENCE_SCOPE:
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
    # If the organizer cannot be determined, fall through and let EWS decide.
    if organizer_email and own_email and organizer_email != own_email:
        raise PermissionDeniedError(
            f"Only the organizer can modify this event (organizer is {organizer_email})"
        )


def _translate_write_error(exc: Exception) -> Exception:
    if isinstance(exc, _PERMISSION_ERRORS):
        return PermissionDeniedError("Only the organizer can modify this event")
    return translate_ews_error(exc)


def _has_attendees(item) -> bool:
    return bool(
        getattr(item, "required_attendees", None)
        or getattr(item, "optional_attendees", None)
    )


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
        new_end = new_start + (item.end - item.start)
    else:
        new_start = item.start
        new_end = parse_datetime(end, "end", config)

    if new_end <= new_start:
        raise InvalidArgumentError(
            f"Invalid range: end {new_end.isoformat()} must be after "
            f"start {new_start.isoformat()}"
        )
    return new_start, new_end


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
    scope: str = _OCCURRENCE_SCOPE,
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
        item.start, item.end = _resolve_new_range(item, start, end, config)

    will_send = bool(send_invitations and _has_attendees(item))

    try:
        item.save(
            send_meeting_invitations=(
                SEND_TO_ALL_AND_SAVE_COPY if will_send else SEND_TO_NONE
            )
        )
    except Exception as exc:
        raise _translate_write_error(exc) from exc

    # Updating an occurrence makes EWS issue a new item id, so the response is
    # built from the item after save() - never from the event_id we were given.
    result = format_event_details(item, config.timezone)
    result["invitations_sent"] = will_send
    return result


def delete_event(
    account,
    config: Config,
    event_id: str,
    *,
    send_cancellations: bool = True,
    scope: str = _OCCURRENCE_SCOPE,
) -> dict:
    item = _load_item(account, event_id)
    _ensure_writable(item, config, scope)

    # Read the subject before deleting - afterwards the item is gone, and the
    # caller needs to be able to say *what* was cancelled.
    subject = getattr(item, "subject", None)
    will_send = bool(send_cancellations and _has_attendees(item))

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
