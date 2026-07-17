class OutlookMcpError(Exception):
    """Base class for errors surfaced to MCP tool callers."""

    code = "unknown_error"

    def to_dict(self) -> dict:
        return {"error": self.code, "message": str(self)}


class ConnectionUnavailableError(OutlookMcpError):
    code = "connection_unavailable"


class AuthenticationError(OutlookMcpError):
    code = "authentication_error"


class ThrottlingError(OutlookMcpError):
    code = "throttling_error"


class ItemNotFoundError(OutlookMcpError):
    code = "item_not_found"


class InvalidArgumentError(OutlookMcpError):
    code = "invalid_argument"


class ConfigurationError(OutlookMcpError):
    code = "configuration_error"
