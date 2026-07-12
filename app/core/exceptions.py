class MarketSignalError(Exception):
    """Base exception for application-level failures."""


class DataProviderError(MarketSignalError):
    """Raised when an external data provider cannot return usable data."""

    def __init__(self, message: str, category: str = "provider_error", retryable: bool = False):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class DataValidationError(MarketSignalError):
    """Raised when incoming market data is malformed."""
