from datetime import date

import pytest
from exchangelib.errors import ErrorInvalidChangeKey, TransportError

from outlook_mcp.config import Config
from outlook_mcp.errors import InvalidArgumentError, ItemNotFoundError
from outlook_mcp.mail_service import get_email_by_id, list_emails, search_emails

from .conftest import make_email


class FakeQuerySet(list):
    def order_by(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self


class FakeFolder:
    def __init__(self, items):
        self._items = items

    def filter(self, *_args, **_kwargs):
        return FakeQuerySet(self._items)


class RaisingFolder:
    def filter(self, *_args, **_kwargs):
        raise TransportError("boom")


class FakeAccount:
    def __init__(self, inbox_items=None, fetch_result=None, fetch_error=None):
        self.inbox = FakeFolder(inbox_items or [])
        self.sent = FakeFolder([])
        self.drafts = FakeFolder([])
        self.junk = FakeFolder([])
        self.trash = FakeFolder([])
        self._fetch_result = fetch_result
        self._fetch_error = fetch_error

    def fetch(self, ids):
        if self._fetch_error is not None:
            raise self._fetch_error
        if self._fetch_result is None:
            return []
        return [self._fetch_result]


class RaisingAccount:
    def __init__(self):
        self.inbox = RaisingFolder()


def make_config():
    cfg = Config()
    cfg.timezone = "Europe/Moscow"
    cfg.default_limit = 50
    cfg.max_limit = 200
    return cfg


def test_list_emails_normal():
    account = FakeAccount(inbox_items=[make_email(subject="Weekly report")])
    result = list_emails(account, make_config())
    assert len(result["emails"]) == 1
    assert result["emails"][0]["subject"] == "Weekly report"
    assert result["has_more"] is False


def test_list_emails_empty_result():
    account = FakeAccount(inbox_items=[])
    result = list_emails(account, make_config())
    assert result["emails"] == []
    assert result["has_more"] is False


def test_list_emails_applies_limit():
    items = [make_email(subject=f"E{i}", item_id=f"id{i}") for i in range(5)]
    account = FakeAccount(inbox_items=items)
    result = list_emails(account, make_config(), limit=2)
    assert len(result["emails"]) == 2
    assert result["has_more"] is True


def test_list_emails_unknown_folder_raises_invalid_argument():
    account = FakeAccount()
    with pytest.raises(InvalidArgumentError):
        list_emails(account, make_config(), folder="NotAFolder")


def test_list_emails_translates_transport_errors():
    account = RaisingAccount()
    from outlook_mcp.errors import ConnectionUnavailableError

    with pytest.raises(ConnectionUnavailableError):
        list_emails(account, make_config())


def test_search_emails_normal():
    account = FakeAccount(inbox_items=[make_email(subject="Invoice #42")])
    result = search_emails(account, make_config(), query="Invoice")
    assert len(result["emails"]) == 1


def test_search_emails_empty_result():
    account = FakeAccount(inbox_items=[])
    result = search_emails(account, make_config(), query="nothing")
    assert result["emails"] == []


def test_get_email_by_id_fetches_with_changekey():
    email = make_email(subject="Details", item_id="EEE", changekey="FFF")
    account = FakeAccount(fetch_result=email)
    result = get_email_by_id(account, "EEE:FFF", make_config())
    assert result["subject"] == "Details"
    assert result["email_id"] == "EEE:FFF"


def test_get_email_by_id_falls_back_when_changekey_stale():
    email = make_email(subject="Recovered", item_id="EEE", changekey="NEWKEY")
    account = FakeAccount(inbox_items=[email], fetch_error=ErrorInvalidChangeKey("stale"))
    result = get_email_by_id(account, "EEE:OLDKEY", make_config())
    assert result["subject"] == "Recovered"


def test_get_email_by_id_not_found_raises():
    account = FakeAccount(fetch_result=None)
    with pytest.raises(ItemNotFoundError):
        get_email_by_id(account, "MISSING:KEY", make_config())
