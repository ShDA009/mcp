import pytest

from outlook_mcp.config import Config, ConfigError


def test_validate_passes_with_all_required_vars(monkeypatch):
    monkeypatch.setenv("EWS_URL", "https://mail.example.com/EWS/Exchange.asmx")
    monkeypatch.setenv("EWS_USERNAME", "user")
    monkeypatch.setenv("EWS_EMAIL", "user@example.com")
    monkeypatch.setenv("EWS_PASSWORD", "secret")
    cfg = Config()
    cfg.validate()


def test_validate_raises_when_missing_vars(monkeypatch):
    monkeypatch.delenv("EWS_URL", raising=False)
    monkeypatch.delenv("EWS_USERNAME", raising=False)
    monkeypatch.delenv("EWS_EMAIL", raising=False)
    monkeypatch.delenv("EWS_PASSWORD", raising=False)
    cfg = Config()
    with pytest.raises(ConfigError):
        cfg.validate()


def test_defaults_applied(monkeypatch):
    monkeypatch.delenv("OUTLOOK_MCP_TIMEZONE", raising=False)
    monkeypatch.delenv("OUTLOOK_MCP_DEFAULT_LIMIT", raising=False)
    monkeypatch.delenv("OUTLOOK_MCP_MAX_LIMIT", raising=False)
    cfg = Config()
    assert cfg.timezone == "Europe/Moscow"
    assert cfg.default_limit == 50
    assert cfg.max_limit == 200
