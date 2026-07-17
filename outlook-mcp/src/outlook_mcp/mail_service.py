from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from exchangelib.errors import ErrorInvalidChangeKey, ErrorInvalidIdMalformed, ErrorItemNotFound
from exchangelib.restriction import Q

from .config import Config
from .errors import InvalidArgumentError, ItemNotFoundError
from .ews_client import translate_ews_error
from .formatting import decode_item_id, format_email_details, format_email_summary

_STALE_ID_ERRORS = (ErrorInvalidChangeKey, ErrorItemNotFound, ErrorInvalidIdMalformed)

_FOLDER_ATTRS = {
    "inbox": "inbox",
    "sent": "sent",
    "drafts": "drafts",
    "junk": "junk",
    "deleted": "trash",
}


def _resolve_folder(account, folder_name: str):
    attr = _FOLDER_ATTRS.get(folder_name.strip().lower())
    if attr is None:
        raise InvalidArgumentError(
            f"Unsupported folder {folder_name!r}, expected one of {sorted(_FOLDER_ATTRS)}"
        )
    return getattr(account, attr)


def _date_range_filter(date_range: tuple[date, date] | None, config: Config) -> dict:
    if date_range is None:
        return {}
    tz = ZoneInfo(config.timezone)
    start_date, end_date = date_range
    start_dt = datetime.combine(start_date, time.min, tzinfo=tz)
    end_dt = datetime.combine(end_date, time.max, tzinfo=tz)
    return {"datetime_received__gte": start_dt, "datetime_received__lte": end_dt}


def list_emails(
    account,
    config: Config,
    folder: str = "Inbox",
    date_range: tuple[date, date] | None = None,
    unread_only: bool = False,
    limit: int | None = None,
) -> dict:
    effective_limit = limit if limit is not None else config.default_limit
    filters = _date_range_filter(date_range, config)
    if unread_only:
        filters["is_read"] = False

    effective_limit = min(effective_limit, config.max_limit)

    try:
        target_folder = _resolve_folder(account, folder)
        qs = target_folder.filter(**filters).order_by("-datetime_received")
        items = list(qs[: effective_limit + 1])
    except Exception as exc:
        raise translate_ews_error(exc) from exc

    has_more = len(items) > effective_limit
    emails = [format_email_summary(item, config.timezone) for item in items[:effective_limit]]
    return {"emails": emails, "has_more": has_more}


def search_emails(
    account,
    config: Config,
    query: str,
    folder: str = "Inbox",
    date_range: tuple[date, date] | None = None,
    limit: int | None = None,
) -> dict:
    effective_limit = limit if limit is not None else config.default_limit
    effective_limit = min(effective_limit, config.max_limit)
    filters = _date_range_filter(date_range, config)

    try:
        target_folder = _resolve_folder(account, folder)
        text_filter = (
            Q(subject__contains=query)
            | Q(sender__contains=query)
            | Q(body__contains=query)
        )
        qs = target_folder.filter(text_filter, **filters).order_by("-datetime_received")
        items = list(qs[: effective_limit + 1])
    except Exception as exc:
        raise translate_ews_error(exc) from exc

    has_more = len(items) > effective_limit
    emails = [format_email_summary(item, config.timezone) for item in items[:effective_limit]]
    return {"emails": emails, "has_more": has_more}


def get_email_by_id(account, email_id: str, config: Config) -> dict:
    item_id, changekey = decode_item_id(email_id)

    item = None
    stale_changekey = False
    if changekey:
        item, stale_changekey = _fetch_one(account, item_id, changekey)

    if item is None and (stale_changekey or not changekey):
        item = _find_by_id_in_mailbox(account, item_id)

    if item is None:
        raise ItemNotFoundError(f"Email with id {email_id!r} was not found")

    return format_email_details(item, config.timezone)


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


def _find_by_id_in_mailbox(account, item_id: str):
    tz = ZoneInfo("UTC")
    window_start = datetime.now(tz) - timedelta(days=_ID_RESOLUTION_WINDOW_DAYS)
    try:
        for folder_attr in _FOLDER_ATTRS.values():
            folder = getattr(account, folder_attr)
            qs = folder.filter(datetime_received__gte=window_start)
            for item in qs:
                if item.id == item_id:
                    return item
    except Exception as exc:
        raise translate_ews_error(exc) from exc
    return None
