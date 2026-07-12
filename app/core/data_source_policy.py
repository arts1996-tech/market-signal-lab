"""Versioned, deterministic policy for selecting analysis price sources."""

SOURCE_POLICY_VERSION = "source_priority_v1"
DEMO_SOURCE = "sample"


def allowed_sources(asset_type: str, currency: str | None, timeframe: str) -> tuple[str, ...]:
    """Return sources in priority order; an empty tuple means unsupported data."""
    if timeframe != "1d":
        return ()
    if asset_type in {"index", "fx"}:
        return ("fred",)
    if asset_type in {"stock", "etf"} and currency == "JPY":
        return ("jquants",)
    return ()


def source_priority(asset_type: str, currency: str | None, timeframe: str, source: str) -> int | None:
    try:
        return allowed_sources(asset_type, currency, timeframe).index(source)
    except ValueError:
        return None
