from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo


@dataclass
class FakeMailbox:
    name: str
    email_address: str


@dataclass
class FakeAttendee:
    mailbox: FakeMailbox
    response_type: str = "Unknown"


@dataclass
class FakeBody:
    text: str
    body_type: str = "Text"

    def __str__(self) -> str:
        return self.text


@dataclass
class FakeCalendarItem:
    id: str
    changekey: str
    subject: str
    start: datetime
    end: datetime
    organizer: FakeMailbox | None = None
    required_attendees: list = field(default_factory=list)
    optional_attendees: list = field(default_factory=list)
    my_response_type: str = "Organizer"
    location: str | None = None
    body: FakeBody | None = None
    is_recurring: bool = False
    recurrence: object = None
    type: str = "Single"


@dataclass
class FakeAttachment:
    name: str
    content_type: str
    size: int


@dataclass
class FakeEmail:
    id: str
    changekey: str
    subject: str
    sender: FakeMailbox | None = None
    datetime_received: datetime | None = None
    is_read: bool = False
    has_attachments: bool = False
    body: FakeBody | None = None
    attachments: list = field(default_factory=list)


def utc_dt(*args) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo("UTC"))


def make_event(
    subject="Sync",
    start=None,
    end=None,
    attendees=None,
    organizer=None,
    location="Room 1",
    body_text="Agenda here",
    item_id="AAA",
    changekey="CCC",
):
    start = start or utc_dt(2026, 7, 15, 10, 0)
    end = end or utc_dt(2026, 7, 15, 11, 0)
    organizer = organizer or FakeMailbox(name="Boss", email_address="boss@example.com")
    return FakeCalendarItem(
        id=item_id,
        changekey=changekey,
        subject=subject,
        start=start,
        end=end,
        organizer=organizer,
        required_attendees=attendees or [],
        location=location,
        body=FakeBody(body_text) if body_text is not None else None,
    )


def make_email(
    subject="Hello",
    sender=None,
    received=None,
    is_read=False,
    has_attachments=False,
    body_text="Email body",
    attachments=None,
    item_id="EEE",
    changekey="FFF",
):
    sender = sender or FakeMailbox(name="Alice", email_address="alice@example.com")
    received = received or utc_dt(2026, 7, 15, 9, 0)
    return FakeEmail(
        id=item_id,
        changekey=changekey,
        subject=subject,
        sender=sender,
        datetime_received=received,
        is_read=is_read,
        has_attachments=has_attachments,
        body=FakeBody(body_text) if body_text is not None else None,
        attachments=attachments or [],
    )
