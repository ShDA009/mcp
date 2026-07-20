from datetime import datetime
from zoneinfo import ZoneInfo

RESPONSE_STATUS_MAP = {
    "Accept": "accepted",
    "Tentative": "tentative",
    "Decline": "declined",
    "NoResponseReceived": "none",
    "Unknown": "none",
    "Organizer": "accepted",
}


def to_timezone_iso(dt: datetime, timezone: str) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo(timezone)).isoformat()


def map_response_status(raw_status: str | None) -> str:
    return RESPONSE_STATUS_MAP.get(raw_status or "Unknown", "none")


def format_attendee(attendee, timezone: str) -> dict:
    mailbox = getattr(attendee, "mailbox", None)
    return {
        "name": getattr(mailbox, "name", None),
        "email": getattr(mailbox, "email_address", None),
        "response_status": map_response_status(getattr(attendee, "response_type", None)),
    }


def format_event_summary(item, timezone: str) -> dict:
    organizer = getattr(item, "organizer", None)
    required = getattr(item, "required_attendees", None) or []
    optional = getattr(item, "optional_attendees", None) or []
    attendees = [format_attendee(a, timezone) for a in list(required) + list(optional)]

    return {
        "event_id": encode_item_id(item),
        "subject": getattr(item, "subject", None),
        "start": to_timezone_iso(item.start, timezone) if item.start else None,
        "end": to_timezone_iso(item.end, timezone) if item.end else None,
        "organizer": {
            "name": getattr(organizer, "name", None),
            "email": getattr(organizer, "email_address", None),
        }
        if organizer
        else None,
        "attendees": attendees,
        "response_status": map_response_status(getattr(item, "my_response_type", None)),
        "location": getattr(item, "location", None),
        "is_recurring": getattr(item, "is_recurring", False) or bool(
            getattr(item, "recurrence", None)
        ),
        "item_type": getattr(item, "type", None),
    }


def format_event_details(item, timezone: str) -> dict:
    summary = format_event_summary(item, timezone)
    body = getattr(item, "body", None)
    summary.update({"body": _body_to_text(body)})
    return summary


def _body_to_text(body) -> str:
    if body is None:
        return ""
    text = str(body)
    if getattr(body, "body_type", None) == "HTML":
        return html_to_text(text)
    return text


def html_to_text(html: str) -> str:
    import re

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace(
        "&gt;", ">"
    )
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def encode_item_id(item) -> str:
    iid = getattr(item, "id", None)
    changekey = getattr(item, "changekey", None)
    return f"{iid}:{changekey}" if changekey else str(iid)


def decode_item_id(event_id: str) -> tuple[str, str | None]:
    if ":" in event_id:
        iid, changekey = event_id.split(":", 1)
        return iid, changekey
    return event_id, None


def apply_limit(items: list, limit: int, max_limit: int) -> tuple[list, bool]:
    effective_limit = min(limit, max_limit)
    has_more = len(items) > effective_limit
    return items[:effective_limit], has_more


def format_email_summary(item, timezone: str) -> dict:
    sender = getattr(item, "sender", None)
    received = getattr(item, "datetime_received", None)
    return {
        "email_id": encode_item_id(item),
        "subject": getattr(item, "subject", None),
        "sender": {
            "name": getattr(sender, "name", None),
            "email": getattr(sender, "email_address", None),
        }
        if sender
        else None,
        "date": to_timezone_iso(received, timezone) if received else None,
        "is_read": bool(getattr(item, "is_read", False)),
        "has_attachments": bool(getattr(item, "has_attachments", False)),
    }


def format_attachment_metadata(attachment) -> dict:
    return {
        "name": getattr(attachment, "name", None),
        "content_type": getattr(attachment, "content_type", None),
        "size": getattr(attachment, "size", None),
    }


def format_email_details(item, timezone: str) -> dict:
    summary = format_email_summary(item, timezone)
    body = getattr(item, "body", None)
    attachments = getattr(item, "attachments", None) or []
    summary.update(
        {
            "body": _body_to_text(body),
            "attachments": [format_attachment_metadata(a) for a in attachments],
        }
    )
    return summary
