import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from exchangelib import CalendarItem, EWSDate, EWSDateTime, EWSTimeZone
from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY, SEND_TO_NONE

from .config import Config
from .errors import InvalidArgumentError
from .ews_client import translate_ews_error
from .formatting import format_event_details

logger = logging.getLogger(__name__)

_DATETIME_HINT = "expected an ISO datetime like 2026-08-20T15:00"
_DATE_HINT = "expected a date like 2026-08-20"


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
