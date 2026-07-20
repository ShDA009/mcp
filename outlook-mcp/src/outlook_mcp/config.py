import os
import platform
from pathlib import Path

from .errors import ConfigurationError

ConfigError = ConfigurationError

# Установочные скрипты (install/setup.sh, setup.ps1) пишут креды сюда, а не в
# env-секцию cline_mcp_settings.json — так секреты не дублируются в JSON-конфиге
# Cline. Читаем файл сами, только для переменных, которых ещё нет в os.environ
# (явный env, если задан, имеет приоритет). Путь должен совпадать с тем, что
# пишет соответствующий install-скрипт: setup.ps1 -> %USERPROFILE%\.outlook-mcp\.env,
# setup.sh (macOS/Linux) -> ~/.config/outlook-mcp/.env.
if platform.system() == "Windows":
    _ENV_FILE = Path.home() / ".outlook-mcp" / ".env"
else:
    _ENV_FILE = Path.home() / ".config" / "outlook-mcp" / ".env"


def _read_env_file(path: Path) -> dict:
    values: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


class Config:
    def __init__(self) -> None:
        file_values = _read_env_file(_ENV_FILE)

        def _get(name: str, default: str = "") -> str:
            return os.environ.get(name) or file_values.get(name, default)

        self.ews_url = _get("EWS_URL")
        self.ews_username = _get("EWS_USERNAME")
        self.ews_password = _get("EWS_PASSWORD")
        self.ews_email = _get("EWS_EMAIL")
        self.timezone = _get("OUTLOOK_MCP_TIMEZONE", "Europe/Moscow")
        self.default_limit = int(_get("OUTLOOK_MCP_DEFAULT_LIMIT", "50"))
        self.max_limit = int(_get("OUTLOOK_MCP_MAX_LIMIT", "200"))

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("EWS_URL", self.ews_url),
                ("EWS_USERNAME", self.ews_username),
                ("EWS_PASSWORD", self.ews_password),
                ("EWS_EMAIL", self.ews_email),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"Missing required environment variables: {', '.join(missing)}"
            )


def load_config() -> Config:
    return Config()
