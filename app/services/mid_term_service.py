"""Point-in-time medium-term analysis from persisted fundamentals and prices."""

from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.fundamentals import derive_fundamental_metrics, fundamentals_as_of
from app.analysis.market_calendar import latest_contiguous_exchange_observations
from app.backtest.audit import frame_hash, json_value, stable_payload_hash
from app.database.models import Asset, FundamentalSnapshot
from app.database.repositories import market_prices_frame


MID_TERM_RULE_VERSION = "mid-term-point-in-time-v1"


def _growth(current, previous) -> float | None:
    if current is None or previous in (None, 0) or pd.isna(current) or pd.isna(previous):
        return None
    return float(current) / float(previous) - 1


def _momentum_metrics(prices: pd.DataFrame) -> dict:
    empty = {
        "momentum_3m": None,
        "momentum_6m": None,
        "momentum_12m": None,
        "distance_from_52week_high": None,
        "contiguous_sessions": 0,
    }
    if prices.empty:
        return empty
    frame = prices.copy()
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True).dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close"]).drop_duplicates("price_time").sort_values("price_time")
    contiguous = latest_contiguous_exchange_observations(frame["price_time"], "XTKS")
    close = frame.tail(contiguous)["close"].reset_index(drop=True)
    result = {**empty, "contiguous_sessions": contiguous}
    for label, sessions in (("momentum_3m", 63), ("momentum_6m", 126), ("momentum_12m", 252)):
        if len(close) >= sessions + 1 and float(close.iloc[-sessions - 1]) > 0:
            result[label] = float(close.iloc[-1] / close.iloc[-sessions - 1] - 1)
    if len(close) >= 252:
        high = float(close.tail(252).max())
        result["distance_from_52week_high"] = float(close.iloc[-1] / high - 1) if high > 0 else None
    return result


def build_mid_term_analysis(
    fundamentals: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    as_of=None,
) -> dict:
    cutoff = pd.Timestamp(as_of or datetime.now(UTC))
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    available = fundamentals_as_of(fundamentals, cutoff)
    if available.empty:
        return {
            "status": "insufficient_data",
            "reasons": ["no_disclosed_fundamentals_as_of_analysis_time"],
            "rule_version": MID_TERM_RULE_VERSION,
            "as_of": cutoff.isoformat(),
            "results": [],
            "input_data_version": stable_payload_hash([]),
        }
    price_pool = prices.copy()
    if not price_pool.empty:
        price_pool["price_time"] = pd.to_datetime(price_pool["price_time"], utc=True)
        price_pool = price_pool[price_pool["price_time"] <= cutoff]
    rows = []
    for symbol, group in available.groupby("symbol"):
        ordered = group.sort_values(["period_end", "disclosed_at"])
        current = ordered.iloc[-1].to_dict()
        previous_rows = ordered[ordered["period_end"] < current["period_end"]]
        previous = previous_rows.iloc[-1].to_dict() if not previous_rows.empty else {}
        symbol_prices = (
            price_pool[price_pool["symbol"] == symbol].sort_values("price_time")
            if not price_pool.empty
            else pd.DataFrame()
        )
        latest_price = (
            float(pd.to_numeric(symbol_prices["close"], errors="coerce").dropna().iloc[-1])
            if not symbol_prices.empty and not pd.to_numeric(symbol_prices["close"], errors="coerce").dropna().empty
            else None
        )
        details = current.get("details") if isinstance(current.get("details"), dict) else {}
        warnings = []
        if not details.get("currency"):
            warnings.append("currency_unknown")
        if not details.get("unit"):
            warnings.append("unit_unknown")
        disclosed = pd.Timestamp(current["disclosed_at"])
        disclosed = disclosed.tz_localize("UTC") if disclosed.tzinfo is None else disclosed.tz_convert("UTC")
        if (cutoff - disclosed).days > 400:
            warnings.append("fundamentals_stale")
        ratios = derive_fundamental_metrics(current, latest_price)
        momentum = _momentum_metrics(symbol_prices)
        if momentum["momentum_3m"] is None:
            warnings.append("insufficient_3m_price_history")
        if momentum["momentum_6m"] is None:
            warnings.append("insufficient_6m_price_history")
        if momentum["momentum_12m"] is None:
            warnings.append("insufficient_12m_price_history")
        rows.append(
            {
                "symbol": symbol,
                "source": current.get("source"),
                "disclosed_at": disclosed.isoformat(),
                "period_end": str(current.get("period_end")),
                "currency": details.get("currency"),
                "unit": details.get("unit"),
                "sales_growth": _growth(current.get("sales"), previous.get("sales")),
                "operating_profit_growth": _growth(
                    current.get("operating_profit"), previous.get("operating_profit")
                ),
                "eps_growth": _growth(current.get("eps"), previous.get("eps")),
                "operating_margin": ratios["operating_margin"],
                "roe": ratios["roe"],
                "equity_ratio": (
                    float(current["equity"]) / float(current["total_assets"])
                    if current.get("equity") is not None
                    and current.get("total_assets") not in (None, 0)
                    else None
                ),
                "operating_cashflow": current.get("operating_cashflow"),
                **momentum,
                "warnings": warnings,
            }
        )
    usable_fields = (
        "sales_growth",
        "operating_profit_growth",
        "eps_growth",
        "operating_margin",
        "roe",
        "equity_ratio",
        "operating_cashflow",
        "momentum_3m",
    )
    has_usable_result = any(
        any(row.get(field) is not None for field in usable_fields) for row in rows
    )
    return {
        "status": "success" if has_usable_result else "insufficient_data",
        "reasons": [] if has_usable_result else ["no_usable_mid_term_metrics"],
        "rule_version": MID_TERM_RULE_VERSION,
        "as_of": cutoff.isoformat(),
        "results": json_value(rows),
        "input_data_version": stable_payload_hash(
            {"fundamentals": frame_hash(available), "prices": frame_hash(price_pool)}
        ),
    }


def run_mid_term_analysis(session: Session, *, as_of=None) -> dict:
    rows = session.execute(
        select(Asset.symbol, FundamentalSnapshot)
        .join(FundamentalSnapshot, FundamentalSnapshot.asset_id == Asset.id)
        .order_by(Asset.symbol, FundamentalSnapshot.disclosed_at)
    ).all()
    fundamentals = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "disclosed_at": snapshot.disclosed_at,
                "period_end": snapshot.period_end,
                "source": snapshot.source,
                "sales": snapshot.sales,
                "operating_profit": snapshot.operating_profit,
                "net_income": snapshot.net_income,
                "eps": snapshot.eps,
                "equity": snapshot.equity,
                "total_assets": snapshot.total_assets,
                "operating_cashflow": snapshot.operating_cashflow,
                "details": snapshot.details or {},
            }
            for symbol, snapshot in rows
        ]
    )
    symbols = sorted(fundamentals["symbol"].unique()) if not fundamentals.empty else []
    prices = market_prices_frame(session, symbols, source_policy="real_only") if symbols else pd.DataFrame()
    result = build_mid_term_analysis(fundamentals, prices, as_of=as_of)
    result["fundamental_snapshots"] = len(fundamentals)
    result["symbols"] = len(symbols)
    return result
