class MarketSignalError(Exception):
    """Base exception for application-level failures."""


class DataProviderError(MarketSignalError):
    """Raised when an external data provider cannot return usable data."""


class DataValidationError(MarketSignalError):
    """Raised when incoming market data is malformed."""

