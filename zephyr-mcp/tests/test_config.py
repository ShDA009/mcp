import pytest

from zephyr_mcp.config import load_config


def test_load_config_reads_env(monkeypatch):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "secret-token")
    cfg = load_config()
    assert cfg.base_url == "https://jira.example.com"
    assert cfg.api_token == "secret-token"


def test_load_config_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com/")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "secret-token")
    cfg = load_config()
    assert cfg.base_url == "https://jira.example.com"


def test_load_config_missing_base_url_fails_fast(monkeypatch):
    monkeypatch.delenv("ZEPHYR_BASE_URL", raising=False)
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "secret-token")
    with pytest.raises(SystemExit, match="ZEPHYR_BASE_URL"):
        load_config()


def test_load_config_missing_api_token_fails_fast(monkeypatch):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.delenv("ZEPHYR_API_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="ZEPHYR_API_TOKEN"):
        load_config()


def test_config_repr_does_not_leak_token(monkeypatch):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "super-secret-token")
    cfg = load_config()
    assert "super-secret-token" not in repr(cfg)
