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
    is_all_day: bool = False


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
    is_all_day=False,
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
        is_all_day=is_all_day,
    )


class RecordingCalendarItem(FakeCalendarItem):
    """Fake CalendarItem that records how save()/delete() were called.

    Mirrors the real exchangelib contract: one save() for both create and
    update (it branches on self.id), and the same kwarg names/defaults. Tests
    assert on the recorded kwargs, not just on the outcome.
    """

    # exchangelib rebuilds item._id from the UpdateItem response; the real
    # class uses ItemId, a two-field (id, changekey) element.
    ID_ELEMENT_CLS = staticmethod(lambda item_id, changekey: (item_id, changekey))

    def __init__(self, *args, save_error=None, delete_error=None, account=None, **kwargs):
        self.saved_with = None
        self.deleted_with = None
        self.save_error = save_error
        self.delete_error = delete_error
        self.account = account
        super().__init__(*args, **kwargs)

    @property
    def _id(self):
        return (self.id, self.changekey)

    @_id.setter
    def _id(self, value):
        self.id, self.changekey = value

    def save(
        self,
        update_fields=None,
        conflict_resolution="AutoResolve",
        send_meeting_invitations="SendToNone",
    ):
        if self.save_error is not None:
            raise self.save_error
        self.saved_with = {
            "update_fields": update_fields,
            "conflict_resolution": conflict_resolution,
            "send_meeting_invitations": send_meeting_invitations,
        }
        if not self.id:
            self.id = "NEW-ID"
            self.changekey = "NEW-CK"
        return self

    def delete(
        self,
        send_meeting_cancellations="SendToNone",
        affected_task_occurrences="AllOccurrences",
        suppress_read_receipts=True,
    ):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted_with = {"send_meeting_cancellations": send_meeting_cancellations}


class FakeUpdateItem:
    """Stand-in for exchangelib's UpdateItem service.

    Mirrors the real contract: call() takes (item, fieldnames) pairs and the
    same kwarg names, and returns (id, changekey) tuples - Exchange reissues
    the id on every edit. Tests patch calendar_write.UpdateItem with this and
    assert on what was recorded onto the item.
    """

    def __init__(self, account=None):
        self.account = account

    def call(
        self,
        items,
        message_disposition,
        conflict_resolution,
        send_meeting_invitations_or_cancellations,
        suppress_read_receipts=True,
    ):
        results = []
        for item, fieldnames in items:
            if getattr(item, "save_error", None) is not None:
                raise item.save_error
            item.saved_with = {
                "update_fields": list(fieldnames),
                "message_disposition": message_disposition,
                "conflict_resolution": conflict_resolution,
                "send_meeting_invitations": send_meeting_invitations_or_cancellations,
            }
            results.append(("UPDATED-ID", "UPDATED-CK"))
        return results


def make_writable_event(
    subject="Sync",
    start=None,
    end=None,
    organizer_email="me@example.com",
    attendees=None,
    item_id="AAA",
    changekey="CCC",
    item_type="Single",
    is_all_day=False,
    save_error=None,
    delete_error=None,
):
    """Build a RecordingCalendarItem organized by me@example.com by default.

    Default attendees are non-empty: invitation/cancellation defaults only have
    an observable effect when there is somebody to notify.
    """
    if attendees is None:
        attendees = [FakeAttendee(FakeMailbox(name="A", email_address="a@example.com"))]
    return RecordingCalendarItem(
        id=item_id,
        changekey=changekey,
        subject=subject,
        start=start or utc_dt(2026, 8, 20, 10, 0),
        end=end or utc_dt(2026, 8, 20, 11, 0),
        organizer=FakeMailbox(name="Me", email_address=organizer_email),
        required_attendees=list(attendees),
        type=item_type,
        is_all_day=is_all_day,
        save_error=save_error,
        delete_error=delete_error,
    )


class _FakeWriteCalendar:
    """Calendar stub backing the stale-ChangeKey fallback scan.

    The scan goes through view(), which is what expands a recurring series into
    individual occurrences - filter() would only ever yield the series master.
    """

    def __init__(self, items):
        self._items = items

    def view(self, start, end, max_items=None):
        items = self._items if max_items is None else self._items[:max_items]
        return list(items)


class FakeWriteAccount:
    """Account stub for write paths: fetch() returns a prepared item.

    When fetch() comes up empty the production code falls back to scanning the
    calendar (a ChangeKey may be stale), so the stub has to offer a calendar
    too - an empty one means "really not found".
    """

    def __init__(self, item=None, fetch_error=None, calendar_items=None):
        self._item = item
        self._fetch_error = fetch_error
        self.calendar = _FakeWriteCalendar(calendar_items or [])

    def fetch(self, ids):
        if self._fetch_error is not None:
            raise self._fetch_error
        return [] if self._item is None else [self._item]


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
