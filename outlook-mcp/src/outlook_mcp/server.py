import logging
from datetime import date

from mcp.server.fastmcp import FastMCP

from .calendar_service import find_free_slots as find_free_slots_svc, get_event_by_id, list_events_for_range
from .config import load_config
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
def find_free_slots(target_date: str, duration_min: int) -> dict:
    """Find free time slots of the given duration (minutes) within working hours on a given date (YYYY-MM-DD)."""
    try:
        day = _parse_date(target_date, "target_date")
        if duration_min <= 0:
            raise InvalidArgumentError(f"Invalid duration_min {duration_min!r}, must be a positive integer")
        account = get_account()
        result = find_free_slots_svc(account, day, duration_min, _config)
        logger.info("find_free_slots returned %d slot(s)", len(result["slots"]))
        return result
    except OutlookMcpError as exc:
        logger.error("find_free_slots failed: %s", exc.code)
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
