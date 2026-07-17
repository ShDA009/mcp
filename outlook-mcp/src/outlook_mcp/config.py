import os

from .errors import ConfigurationError

ConfigError = ConfigurationError


class Config:
    def __init__(self) -> None:
        self.ews_url = os.environ.get("EWS_URL", "")
        self.ews_username = os.environ.get("EWS_USERNAME", "")
        self.ews_password = os.environ.get("EWS_PASSWORD", "")
        self.ews_email = os.environ.get("EWS_EMAIL", "")
        self.timezone = os.environ.get("OUTLOOK_MCP_TIMEZONE", "Europe/Moscow")
        self.default_limit = int(os.environ.get("OUTLOOK_MCP_DEFAULT_LIMIT", "50"))
        self.max_limit = int(os.environ.get("OUTLOOK_MCP_MAX_LIMIT", "200"))

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
