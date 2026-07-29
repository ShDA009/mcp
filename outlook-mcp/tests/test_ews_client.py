from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from exchangelib.errors import TransportError, UnauthorizedError

from outlook_mcp.config import Config
from outlook_mcp.errors import AuthenticationError, ConnectionUnavailableError
from outlook_mcp.ews_client import build_account


def make_config():
    cfg = Config()
    cfg.ews_url = "https://mail.example.com/EWS/Exchange.asmx"
    cfg.ews_username = "user"
    cfg.ews_email = "user@example.com"
    cfg.ews_password = "secret"
    return cfg


def test_build_account_raises_authentication_error_on_unauthorized():
    with patch("outlook_mcp.ews_client.Account", side_effect=UnauthorizedError("bad creds")):
        with pytest.raises(AuthenticationError):
            build_account(make_config())


def test_build_account_raises_connection_unavailable_on_transport_error():
    with patch("outlook_mcp.ews_client.Account", side_effect=TransportError("no route")):
        with pytest.raises(ConnectionUnavailableError):
            build_account(make_config())


def test_build_account_forces_version_guessing():
    """account.version must be touched so config.version is resolved eagerly.

    With autodiscover=False, Account.__init__ leaves config.version as None;
    it's only populated as a side effect of accessing account.version (or
    account.protocol.version). Services called directly via
    account.protocol.* (get_free_busy_info, resolve_names) skip every other
    Account.* attribute access, so if build_account doesn't force this here,
    the first such call in a session fails with
    AttributeError: 'NoneType' object has no attribute 'api_version'
    deep inside exchangelib's EWSService._version_hint.
    """
    fake_account = MagicMock()
    version_property = PropertyMock(return_value="not-none")
    type(fake_account).version = version_property
    with patch("outlook_mcp.ews_client.Account", return_value=fake_account):
        result = build_account(make_config())

    assert result is fake_account
    version_property.assert_called_once()


def test_build_account_translates_error_from_version_guessing():
    """A transport failure during the forced version-guess must still be
    translated, not leak as a raw exchangelib/urllib exception."""
    fake_account = MagicMock()
    type(fake_account).version = PropertyMock(side_effect=TransportError("no route"))
    with patch("outlook_mcp.ews_client.Account", return_value=fake_account):
        with pytest.raises(ConnectionUnavailableError):
            build_account(make_config())
