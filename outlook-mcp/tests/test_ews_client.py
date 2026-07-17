from unittest.mock import patch

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
