from outlook_mcp.formatting import (
    apply_limit,
    format_email_details,
    format_email_summary,
    format_event_details,
    format_event_summary,
    html_to_text,
    map_response_status,
    to_timezone_iso,
)

from .conftest import FakeAttachment, FakeAttendee, FakeMailbox, make_email, make_event, utc_dt


def test_to_timezone_iso_converts_utc_to_moscow():
    dt = utc_dt(2026, 7, 15, 11, 30)
    result = to_timezone_iso(dt, "Europe/Moscow")
    assert result == "2026-07-15T14:30:00+03:00"


def test_map_response_status_known_values():
    assert map_response_status("Accept") == "accepted"
    assert map_response_status("Tentative") == "tentative"
    assert map_response_status("Decline") == "declined"
    assert map_response_status("NoResponseReceived") == "none"


def test_map_response_status_unknown_defaults_to_none():
    assert map_response_status(None) == "none"
    assert map_response_status("SomethingWeird") == "none"


def test_format_event_summary_normal_event():
    event = make_event()
    result = format_event_summary(event, "Europe/Moscow")
    assert result["subject"] == "Sync"
    assert result["event_id"] == "AAA:CCC"
    assert result["start"] == "2026-07-15T13:00:00+03:00"
    assert result["end"] == "2026-07-15T14:00:00+03:00"
    assert result["organizer"]["email"] == "boss@example.com"
    assert result["location"] == "Room 1"


def test_format_event_summary_multiple_attendees_with_statuses():
    attendees = [
        FakeAttendee(mailbox=FakeMailbox("Alice", "alice@example.com"), response_type="Accept"),
        FakeAttendee(mailbox=FakeMailbox("Bob", "bob@example.com"), response_type="Decline"),
        FakeAttendee(mailbox=FakeMailbox("Carl", "carl@example.com"), response_type="Tentative"),
    ]
    event = make_event(attendees=attendees)
    result = format_event_summary(event, "Europe/Moscow")
    assert len(result["attendees"]) == 3
    statuses = {a["email"]: a["response_status"] for a in result["attendees"]}
    assert statuses["alice@example.com"] == "accepted"
    assert statuses["bob@example.com"] == "declined"
    assert statuses["carl@example.com"] == "tentative"


def test_format_event_details_includes_body_and_recurrence_flag():
    event = make_event(body_text="Discuss roadmap")
    event.is_recurring = True
    result = format_event_details(event, "Europe/Moscow")
    assert result["body"] == "Discuss roadmap"
    assert result["is_recurring"] is True


def test_format_event_details_empty_body():
    event = make_event(body_text=None)
    result = format_event_details(event, "Europe/Moscow")
    assert result["body"] == ""


def test_html_to_text_strips_tags_and_entities():
    html = "<p>Hello&nbsp;<b>world</b></p><br><p>Line2</p>"
    text = html_to_text(html)
    assert "Hello world" in text
    assert "Line2" in text
    assert "<" not in text


def test_apply_limit_no_truncation_needed():
    items = list(range(5))
    limited, has_more = apply_limit(items, limit=10, max_limit=200)
    assert limited == items
    assert has_more is False


def test_apply_limit_truncates_and_flags_has_more():
    items = list(range(10))
    limited, has_more = apply_limit(items, limit=3, max_limit=200)
    assert limited == [0, 1, 2]
    assert has_more is True


def test_apply_limit_respects_hard_cap():
    items = list(range(300))
    limited, has_more = apply_limit(items, limit=250, max_limit=200)
    assert len(limited) == 200
    assert has_more is True


def test_format_event_summary_empty_result_is_not_error():
    events = []
    formatted = [format_event_summary(e, "Europe/Moscow") for e in events]
    assert formatted == []


def test_format_email_summary_normal_email():
    email = make_email(subject="Report", is_read=True, has_attachments=False)
    result = format_email_summary(email, "Europe/Moscow")
    assert result["subject"] == "Report"
    assert result["email_id"] == "EEE:FFF"
    assert result["sender"]["email"] == "alice@example.com"
    assert result["is_read"] is True
    assert result["has_attachments"] is False
    assert result["date"] == "2026-07-15T12:00:00+03:00"


def test_format_email_details_with_attachment():
    attachment = FakeAttachment(name="report.pdf", content_type="application/pdf", size=1024)
    email = make_email(has_attachments=True, attachments=[attachment], body_text="See attached")
    result = format_email_details(email, "Europe/Moscow")
    assert result["body"] == "See attached"
    assert len(result["attachments"]) == 1
    assert result["attachments"][0] == {
        "name": "report.pdf",
        "content_type": "application/pdf",
        "size": 1024,
    }


def test_format_email_details_without_attachment():
    email = make_email(has_attachments=False)
    result = format_email_details(email, "Europe/Moscow")
    assert result["attachments"] == []


def test_format_email_summary_empty_result_is_not_error():
    emails = []
    formatted = [format_email_summary(e, "Europe/Moscow") for e in emails]
    assert formatted == []
