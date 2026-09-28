import pytest

import zephyr_mcp.config as config_module
from zephyr_mcp.config import load_config


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch, tmp_path):
    # Изолируем тесты от реального ~/.config/zephyr-mcp/.env (или его
    # Windows-эквивалента), который мог создать install-скрипт на машине
    # разработчика — тесты должны видеть только os.environ.
    monkeypatch.setattr(config_module, "_ENV_FILE", tmp_path / "nonexistent.env")


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


def test_env_file_fills_missing_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("ZEPHYR_BASE_URL", raising=False)
    monkeypatch.delenv("ZEPHYR_API_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ZEPHYR_BASE_URL=https://jira.example.com\n"
        "ZEPHYR_API_TOKEN=file-token\n"
    )
    monkeypatch.setattr(config_module, "_ENV_FILE", env_file)
    cfg = load_config()
    assert cfg.base_url == "https://jira.example.com"
    assert cfg.api_token == "file-token"


def test_os_environ_takes_priority_over_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ZEPHYR_API_TOKEN=from_file\n")
    monkeypatch.setattr(config_module, "_ENV_FILE", env_file)
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "from_environ")
    cfg = load_config()
    assert cfg.api_token == "from_environ"


def test_allow_write_defaults_to_false(monkeypatch):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "t")
    monkeypatch.delenv("ZEPHYR_ALLOW_WRITE", raising=False)
    assert load_config().allow_write is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_allow_write_enabled_by_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "t")
    monkeypatch.setenv("ZEPHYR_ALLOW_WRITE", value)
    assert load_config().allow_write is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "", "  "])
def test_allow_write_disabled_by_falsy_values(monkeypatch, value):
    monkeypatch.setenv("ZEPHYR_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("ZEPHYR_API_TOKEN", "t")
    monkeypatch.setenv("ZEPHYR_ALLOW_WRITE", value)
    assert load_config().allow_write is False


def test_allow_write_missing_creds_still_fails_fast(monkeypatch):
    monkeypatch.delenv("ZEPHYR_BASE_URL", raising=False)
    monkeypatch.setenv("ZEPHYR_ALLOW_WRITE", "1")
    with pytest.raises(SystemExit, match="ZEPHYR_BASE_URL"):
        load_config()
