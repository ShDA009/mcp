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


_TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class Config:
    base_url: str
    api_token: str = field(repr=False)
    allow_write: bool = False
    allow_delete: bool = False


def load_config() -> Config:
    file_values = _read_env_file(_ENV_FILE)
    values = {}
    for name in ("ZEPHYR_BASE_URL", "ZEPHYR_API_TOKEN"):
        value = (os.environ.get(name) or file_values.get(name, "")).strip()
        if not value:
            raise SystemExit(f"missing required env var {name}")
        values[name] = value

    # Пишущие tools по умолчанию выключены: Zephyr общий для команды, и
    # обновление сервера не должно молча давать агенту право создавать кейсы.
    raw_allow_write = (
        os.environ.get("ZEPHYR_ALLOW_WRITE")
        or file_values.get("ZEPHYR_ALLOW_WRITE", "")
    ).strip().lower()
    # Удаление необратимо, поэтому ALLOW_WRITE его не включает — нужен отдельный
    # флаг (и он действует только вместе с ALLOW_WRITE).
    raw_allow_delete = (
        os.environ.get("ZEPHYR_ALLOW_DELETE")
        or file_values.get("ZEPHYR_ALLOW_DELETE", "")
    ).strip().lower()

    return Config(
        base_url=values["ZEPHYR_BASE_URL"].rstrip("/"),
        api_token=values["ZEPHYR_API_TOKEN"],
        allow_write=raw_allow_write in _TRUTHY,
        allow_delete=raw_allow_delete in _TRUTHY,
    )
