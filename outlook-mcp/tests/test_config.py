import pytest

import outlook_mcp.config as config_module
from outlook_mcp.config import Config, ConfigError


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch, tmp_path):
    # Изолируем тесты от реального ~/.config/outlook-mcp/.env (или его
    # Windows-эквивалента), который мог создать install-скрипт на машине
    # разработчика — тесты должны видеть только os.environ.
    monkeypatch.setattr(config_module, "_ENV_FILE", tmp_path / "nonexistent.env")


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


def test_env_file_fills_missing_vars(monkeypatch, tmp_path):
    for name in ("EWS_URL", "EWS_USERNAME", "EWS_EMAIL", "EWS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EWS_URL=https://mail.example.com/EWS/Exchange.asmx\n"
        "EWS_USERNAME=user\n"
        "EWS_EMAIL=user@example.com\n"
        "EWS_PASSWORD=secret\n"
    )
    monkeypatch.setattr(config_module, "_ENV_FILE", env_file)
    cfg = Config()
    cfg.validate()
    assert cfg.ews_username == "user"


def test_os_environ_takes_priority_over_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("EWS_USERNAME=from_file\n")
    monkeypatch.setattr(config_module, "_ENV_FILE", env_file)
    monkeypatch.setenv("EWS_USERNAME", "from_environ")
    cfg = Config()
    assert cfg.ews_username == "from_environ"
