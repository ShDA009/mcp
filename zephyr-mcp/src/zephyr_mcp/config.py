import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

# Установочные скрипты (install/setup.sh, setup.ps1) пишут креды сюда, а не в
# env-секцию cline_mcp_settings.json — так секреты не дублируются в JSON-конфиге
# Cline. Читаем файл сами, только для переменных, которых ещё нет в os.environ
# (явный env, если задан, имеет приоритет). Путь должен совпадать с тем, что
# пишет соответствующий install-скрипт: setup.ps1 -> %USERPROFILE%\.zephyr-mcp\.env,
# setup.sh (macOS/Linux) -> ~/.config/zephyr-mcp/.env.
if platform.system() == "Windows":
    _ENV_FILE = Path.home() / ".zephyr-mcp" / ".env"
else:
    _ENV_FILE = Path.home() / ".config" / "zephyr-mcp" / ".env"


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


@dataclass
class Config:
    base_url: str
    api_token: str = field(repr=False)


def load_config() -> Config:
    file_values = _read_env_file(_ENV_FILE)
    values = {}
    for name in ("ZEPHYR_BASE_URL", "ZEPHYR_API_TOKEN"):
        value = (os.environ.get(name) or file_values.get(name, "")).strip()
        if not value:
            raise SystemExit(f"missing required env var {name}")
        values[name] = value

    return Config(
        base_url=values["ZEPHYR_BASE_URL"].rstrip("/"),
        api_token=values["ZEPHYR_API_TOKEN"],
    )
