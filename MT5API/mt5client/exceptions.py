"""Custom exceptions for MT5 client."""


class MT5Error(Exception):
    """Base exception for MT5 client."""
    pass


class AuthError(MT5Error):
    """Authentication failed."""
    pass


class TradeError(MT5Error):
    """Trade operation failed."""
    def __init__(self, retcode: int, message: str = ""):
        self.retcode = retcode
        super().__init__(f"Trade error {retcode}: {message}")


class ServerNotFoundError(MT5Error):
    """Server name not found."""
    pass


class ConnectionError(MT5Error):
    """WebSocket connection failed."""
    pass


class TimeoutError(MT5Error):
    """Operation timed out."""
    pass
