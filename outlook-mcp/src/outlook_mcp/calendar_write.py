import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from exchangelib import EWSDate, EWSDateTime, EWSTimeZone

from .config import Config
from .errors import InvalidArgumentError

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
