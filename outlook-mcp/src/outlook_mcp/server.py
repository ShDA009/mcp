import logging
from datetime import date

from mcp.server.fastmcp import FastMCP

from .calendar_service import find_free_slots as find_free_slots_svc, get_event_by_id, list_events_for_range
from .config import load_config
from .directory_service import resolve_person as resolve_person_svc
from .errors import InvalidArgumentError, OutlookMcpError
from .ews_client import build_account
from .mail_service import get_email_by_id, list_emails as list_emails_svc, search_emails as search_emails_svc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("outlook-mcp")

_config = load_config()
_account = None


def get_account():
    global _account
    if _account is None:
        _config.validate()
        _account = build_account(_config)
    return _account


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidArgumentError(
            f"Invalid {field_name} {value!r}, expected YYYY-MM-DD"
        ) from exc


def _validate_limit(limit: int | None) -> int | None:
    if limit is not None and limit <= 0:
        raise InvalidArgumentError(f"Invalid limit {limit!r}, must be a positive integer")
    return limit


_MAX_FREE_BUSY_EMAILS = 20


def _validate_emails(emails: list[str] | None) -> list[str]:
    """Normalize and validate an optional list of participant SMTP addresses.

    None or an empty list means "own mailbox only". Validation is deliberately
    lenient (just checks for a non-empty local/domain split around "@") since
    Exchange is the authority on whether an address actually resolves - a
    well-formed but unknown address should end up in the "unavailable" field
    of the result, not raise here.
    """
    if emails is None:
        return []
    if not isinstance(emails, list):
        raise InvalidArgumentError(f"Invalid emails {emails!r}, expected a list of email addresses")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in emails:
        if not isinstance(raw, str):
            raise InvalidArgumentError(f"Invalid email {raw!r}, expected a string")
        value = raw.strip().lower()
        if not value or "@" not in value or value.startswith("@") or value.endswith("@"):
            raise InvalidArgumentError(f"Invalid email {raw!r}, expected an address like name@example.com")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    if len(normalized) > _MAX_FREE_BUSY_EMAILS:
        raise InvalidArgumentError(f"Too many emails ({len(normalized)}), maximum is {_MAX_FREE_BUSY_EMAILS}")
    return normalized


@mcp.tool()
def list_events(target_date: str | None = None, end_date: str | None = None) -> dict:
    """List calendar events for a date or date range (YYYY-MM-DD, defaults to today, Europe/Moscow).

    target_date - start of the range (defaults to today).
    end_date - end of the range, inclusive (defaults to target_date, i.e. a single day).
    """
    try:
        start = _parse_date(target_date, "target_date") or date.today()
        end = _parse_date(end_date, "end_date") or start
        account = get_account()
        result = list_events_for_range(account, start, end, _config)
        logger.info("list_events returned %d item(s)", len(result["events"]))
        return result
    except OutlookMcpError as exc:
        logger.error("list_events failed: %s", exc.code)
        return exc.to_dict()


@mcp.tool()
def get_event(event_id: str) -> dict:
    """Get full details of a calendar event by event_id (body, attendees, location, recurrence)."""
    try:
        account = get_account()
        result = get_event_by_id(account, event_id, _config)
        logger.info("get_event succeeded")
        return result
    except OutlookMcpError as exc:
        logger.error("get_event failed: %s", exc.code)
        return exc.to_dict()


@mcp.tool()
def find_free_slots(
    target_date: str,
    duration_min: int,
    emails: list[str] | None = None,
    include_self: bool = True,
    debug: bool = False,
) -> dict:
    """Find free time slots of the given duration (minutes) within working hours on a given date (YYYY-MM-DD).

    emails - optional list of colleagues' SMTP addresses. When given, a slot is
    returned only if every listed person is free. Working hours always come
    from the own mailbox's Exchange settings, not the other participants'.
    Participants whose free/busy cannot be read (no permission, unknown
    mailbox) are skipped and listed in the "unavailable" field of the result
    instead of failing the call.

    include_self - when true (default), the own mailbox's busy time is part of
    the intersection, i.e. the answer is "when can WE meet". Set it to false to
    ask "when are these colleagues free, regardless of my own calendar" - the
    own calendar is then ignored for busy time, but the working hours of the
    day still come from the own mailbox. include_self=false without any emails
    is an error (nobody's calendar would be checked).

    The result has two slot lists. "slots" are fully free. "tentative_slots"
    are free except that someone has a tentatively accepted meeting there -
    they are real candidates worth proposing, but mention that the time is
    tentative. The two lists never overlap.

    The result always includes "reason", explaining an empty "slots" list:
    "ok" (slots found), "no_working_hours_for_weekday" (weekend/non-working
    day), "fully_busy", "no_window_fits_duration" (free time exists but no
    window is long enough), "only_tentative" (only tentative slots exist), or
    "all_participants_unavailable" (include_self=false and every email failed
    - nobody's calendar could actually be checked).

    debug - when true, adds a "diagnostics" field with per-participant
    free/busy details (raw events, busy-type counts, working hours source)
    for troubleshooting an unexpected result. Leave false for normal use -
    it adds a lot of data to the response.
    """
    try:
        day = _parse_date(target_date, "target_date")
        if duration_min <= 0:
            raise InvalidArgumentError(f"Invalid duration_min {duration_min!r}, must be a positive integer")
        if not isinstance(include_self, bool):
            raise InvalidArgumentError(f"Invalid include_self {include_self!r}, expected a boolean")
        if not isinstance(debug, bool):
            raise InvalidArgumentError(f"Invalid debug {debug!r}, expected a boolean")
        participants = _validate_emails(emails)
        account = get_account()
        result = find_free_slots_svc(
            account,
            day,
            duration_min,
            _config,
            emails=participants,
            include_self=include_self,
            debug=debug,
        )
        logger.info(
            "find_free_slots returned %d slot(s), %d tentative, for %d extra participant(s), "
            "%d unavailable, reason=%s (include_self=%s)",
            len(result["slots"]),
            len(result["tentative_slots"]),
            len(participants),
            len(result["unavailable"]),
            result["reason"],
            include_self,
        )
        return result
    except OutlookMcpError as exc:
        logger.error("find_free_slots failed: %s", exc.code)
        return exc.to_dict()


@mcp.tool()
def resolve_person(query: str) -> dict:
    """Look up a colleague's email address by (partial) name in the Exchange address book.

    query - a name or part of a name (e.g. "Ivanov Ivan"), not necessarily an
    email address. Returns a list of candidates ({"name", "email"}); no match
    yields an empty list rather than an error. Use this before find_free_slots
    when you only have a person's name, not their email.
    """
    try:
        if not query or not query.strip():
            raise InvalidArgumentError("query must not be empty")
        account = get_account()
        result = resolve_person_svc(account, query.strip())
        logger.info("resolve_person returned %d candidate(s)", len(result["candidates"]))
        return result
    except OutlookMcpError as exc:
        logger.error("resolve_person failed: %s", exc.code)
        return exc.to_dict()


@mcp.tool()
def list_emails(
    folder: str = "Inbox",
    start_date: str | None = None,
    end_date: str | None = None,
    unread_only: bool = False,
    limit: int | None = None,
) -> dict:
    """List emails in a folder (Inbox/Sent/Drafts/Junk/Deleted), optionally filtered by date range and read status."""
    try:
        date_range = None
        if start_date and end_date:
            date_range = (_parse_date(start_date, "start_date"), _parse_date(end_date, "end_date"))
        limit = _validate_limit(limit)
        account = get_account()
        result = list_emails_svc(
            account, _config, folder=folder, date_range=date_range,
            unread_only=unread_only, limit=limit,
        )
        logger.info("list_emails returned %d item(s)", len(result["emails"]))
        return result
    except OutlookMcpError as exc:
        logger.error("list_emails failed: %s", exc.code)
        return exc.to_dict()


@mcp.tool()
def get_email(email_id: str) -> dict:
    """Get full details of an email by email_id (plain text body, attachment metadata only)."""
    try:
        account = get_account()
        result = get_email_by_id(account, email_id, _config)
        logger.info("get_email succeeded")
        return result
    except OutlookMcpError as exc:
        logger.error("get_email failed: %s", exc.code)
        return exc.to_dict()


@mcp.tool()
def search_emails(
    query: str,
    folder: str = "Inbox",
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
) -> dict:
    """Search emails by subject/sender/body text in a folder, optionally filtered by date range."""
    try:
        date_range = None
        if start_date and end_date:
            date_range = (_parse_date(start_date, "start_date"), _parse_date(end_date, "end_date"))
        limit = _validate_limit(limit)
        account = get_account()
        result = search_emails_svc(
            account, _config, query=query, folder=folder, date_range=date_range, limit=limit,
        )
        logger.info("search_emails returned %d item(s)", len(result["emails"]))
        return result
    except OutlookMcpError as exc:
        logger.error("search_emails failed: %s", exc.code)
        return exc.to_dict()


def main() -> None:
    # Лёгкий режим самопроверки для установочных скриптов: `ews-mcp-server --help`
    # печатает справку и завершается, не открывая stdio-сессию (иначе процесс
    # завис бы в ожидании ввода). Используется в setup.sh / setup.ps1.
    import sys

    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "outlook-mcp — read-only MCP server for Exchange/EWS (stdio transport).\n"
            "Run without arguments to start the MCP stdio server.\n"
            "Required env: EWS_URL, EWS_USERNAME, EWS_EMAIL, EWS_PASSWORD."
        )
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
