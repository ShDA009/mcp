import os
from dataclasses import dataclass, field


@dataclass
class Config:
    base_url: str
    api_token: str = field(repr=False)


def load_config() -> Config:
    values = {}
    for name in ("ZEPHYR_BASE_URL", "ZEPHYR_API_TOKEN"):
        value = os.environ.get(name, "").strip()
        if not value:
            raise SystemExit(f"missing required env var {name}")
        values[name] = value

    return Config(
        base_url=values["ZEPHYR_BASE_URL"].rstrip("/"),
        api_token=values["ZEPHYR_API_TOKEN"],
    )
