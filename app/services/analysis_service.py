from sqlalchemy.orm import Session

from app.analysis.correlation import (
    close_wide,
    conditional_next_day_stats,
    horizon_correlations,
    normalized_index,
    rolling_correlation,
    us_japan_pair_frame,
)
from app.database.repositories import market_prices_frame


DEFAULT_SYMBOLS = ["NASDAQCOM", "DJIA", "SP500", "NIKKEI225", "DEXJPUS"]


def load_market_analysis(session: Session, symbols: list[str] | None = None) -> dict:
    prices = market_prices_frame(session, symbols or DEFAULT_SYMBOLS)
    wide = close_wide(prices)
    pair = us_japan_pair_frame(wide, "NASDAQCOM", "NIKKEI225")
    return {
        "prices": prices,
        "wide": wide,
        "normalized": normalized_index(wide),
        "pair": pair,
        "horizon_correlations": horizon_correlations(pair),
        "rolling_correlation": rolling_correlation(pair, 60),
        "conditional_stats": conditional_next_day_stats(pair),
    }

