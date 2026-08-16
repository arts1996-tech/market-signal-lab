import pandas as pd
import numpy as np

from app.backtest.portfolio import ExecutionAssumptions, simulate_long_portfolio
from app.analysis.movement_candidates import (
    movement_score,
    us_japan_market_context,
)
from app.analysis.market_calendar import (
    exchange_calendar,
    latest_contiguous_exchange_observations,
)
from app.analysis.technical import short_term_indicator_frame


def generate_demo_phase4_data(periods: int = 120, symbol: str = "DEMOJP") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return deterministic, in-memory data for phase-4 UI/engine checks.

    This deliberately never writes to the database and marks every row as ``demo``.
    It must not be used as a substitute for real market observations.
    """
    if periods < 40:
        raise ValueError("periods must be at least 40")
    rng = np.random.default_rng(20260720)
    dates = exchange_calendar("XTKS").sessions_in_range("2025-01-01", "2026-12-31")[:periods]
    us_returns = rng.normal(0.0005, 0.012, periods)
    japan_returns = np.roll(us_returns, 1) * 0.35 + rng.normal(0.0003, 0.014, periods)
    japan_returns[0] = 0.0003
    rows = []
    for name, values, source_symbol in (
        ("NASDAQCOM", 15000 * np.cumprod(1 + us_returns), "NASDAQCOM"),
        ("DJIA", 38000 * np.cumprod(1 + us_returns * 0.8 + rng.normal(0, 0.003, periods)), "DJIA"),
        ("SP500", 5000 * np.cumprod(1 + us_returns * 0.9 + rng.normal(0, 0.002, periods)), "SP500"),
        ("NIKKEI225", 39000 * np.cumprod(1 + japan_returns), "NIKKEI225"),
    ):
        rows.extend(
            {
                "symbol": name,
                "name": name,
                "price_time": date,
                "open": float(value),
                "close": float(value),
                "source": "demo",
                "source_symbol": source_symbol,
                "fetched_at": date,
            }
            for date, value in zip(dates, values, strict=True)
        )
    japan_values = 2500 * np.cumprod(1 + japan_returns * 1.15 + rng.normal(0, 0.004, periods))
    japan_rows = [
        {
            "symbol": symbol,
            "name": "Demo Japan Equity",
            "price_time": date,
            "open": float(value),
            "close": float(value),
            "source": "demo",
            "source_symbol": symbol,
            "fetched_at": date,
        }
        for date, value in zip(dates, japan_values, strict=True)
    ]
    return pd.DataFrame(rows), pd.DataFrame(japan_rows)


def simulate_virtual_account(
    trades: pd.DataFrame,
    initial_cash: float = 2_500_000,
    account_name: str = "short_term",
    allocation_rate: float = 0.25,
    fee_rate: float = 0.001,
    spread_rate: float = 0.001,
    tax_rate: float = 0.0,
    lot_size: int = 100,
    maximum_positions: int = 2,
    price_history: pd.DataFrame | None = None,
    benchmark: pd.Series | None = None,
) -> dict:
    """Simulate a cash-reserving, long-only account without placing orders."""
    assumptions = ExecutionAssumptions(
        fee_rate=fee_rate,
        spread_rate=spread_rate,
        tax_rate=tax_rate,
        lot_size=lot_size,
        maximum_positions=maximum_positions,
        maximum_position_rate=allocation_rate,
    )
    return simulate_long_portfolio(
        trades,
        initial_cash=initial_cash,
        account_name=account_name,
        assumptions=assumptions,
        price_history=price_history,
        benchmark=benchmark,
    )


def virtual_signals_from_reference_trades(
    trades: pd.DataFrame,
    *,
    stop_loss: float = -0.05,
    take_profit: float = 0.08,
    maximum_holding_days: int | None = None,
) -> pd.DataFrame:
    """Remove future outcome fields and create point-in-time execution signals.

    ``build_virtual_trades`` also produces reference outcomes for the evaluation
    table. Those future prices must never be handed to the portfolio engine as
    signal inputs, so this adapter selects only fields known at the decision.
    """

    if trades.empty:
        return pd.DataFrame()
    if stop_loss >= 0 or take_profit <= 0:
        raise ValueError("stop_loss must be negative and take_profit must be positive")
    required = {"signal_date", "entry_date", "symbol", "side"}
    missing = required.difference(trades.columns)
    if missing:
        raise ValueError(f"trades missing required signal columns: {sorted(missing)}")
    rows = []
    for trade in trades.to_dict(orient="records"):
        holding_days = maximum_holding_days or int(trade.get("holding_days", 5))
        rows.append(
            {
                "signal_date": trade["signal_date"],
                "entry_date": trade["entry_date"],
                "symbol": trade["symbol"],
                "name": trade.get("name", trade["symbol"]),
                "sector": trade.get("sector", "unknown"),
                "score": trade.get("score", 0),
                "side": trade["side"],
                "minimum_score": trade.get("score_threshold", 70),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "maximum_holding_days": holding_days,
                "reasons": trade.get("entry_reasons", []),
                "counterarguments": [
                    "財務・イベント・板情報を完全には反映していません",
                    "過去の参考評価は将来の収益を保証しません",
                ],
            }
        )
    return pd.DataFrame(rows)


def build_virtual_trades(
    index_prices: pd.DataFrame,
    japan_prices: pd.DataFrame,
    score_threshold: int = 70,
    holding_days: int = 5,
    min_observations: int = 30,
    max_trades: int | None = 50,
    recent_signal_sessions: int | None = 11,
) -> pd.DataFrame:
    if japan_prices.empty:
        return pd.DataFrame()

    price_frame = japan_prices.copy()
    price_frame["price_time"] = pd.to_datetime(price_frame["price_time"], utc=True).dt.normalize()
    price_frame["close"] = pd.to_numeric(price_frame["close"])
    if "open" not in price_frame:
        return pd.DataFrame()
    price_frame["open"] = pd.to_numeric(price_frame["open"], errors="coerce")

    rows = []
    context_cache = {}
    for symbol, group in price_frame.groupby("symbol"):
        ordered = group.drop_duplicates("price_time").set_index("price_time").sort_index()
        contiguous_observations = latest_contiguous_exchange_observations(
            ordered.index, "XTKS"
        )
        ordered = ordered.tail(contiguous_observations)
        close = ordered["close"]
        open_prices = ordered["open"]
        if len(close) < min_observations + holding_days:
            continue
        indicators = short_term_indicator_frame(close)
        name = group["name"].dropna().iloc[-1] if "name" in group and not group["name"].dropna().empty else symbol
        if recent_signal_sessions is None:
            start_location = min_observations - 1
        else:
            start_location = max(
                min_observations - 1,
                len(close) - holding_days - recent_signal_sessions,
            )
        for location in range(start_location, len(close) - holding_days):
            signal_date = close.index[location]
            row = indicators.loc[signal_date]
            cache_key = pd.Timestamp(signal_date)
            if cache_key not in context_cache:
                historical_index_prices = (
                    index_prices[pd.to_datetime(index_prices["price_time"], utc=True) < signal_date]
                    if not index_prices.empty and "price_time" in index_prices
                    else pd.DataFrame()
                )
                context_cache[cache_key] = us_japan_market_context(historical_index_prices)
            context = context_cache[cache_key]
            score, direction, reasons = movement_score(row, context["average_correlation"], context["us_signal"])
            if score < score_threshold:
                continue
            entry_location = location + 1
            entry_date = close.index[entry_location]
            exit_date = close.index[location + holding_days]
            entry_price = open_prices.iloc[entry_location]
            if pd.isna(entry_price):
                continue
            entry_price = float(entry_price)
            exit_price = float(close.iloc[location + holding_days])
            trade_return = exit_price / entry_price - 1
            side = "long" if "上方向" in direction else "observe_only"
            rows.append(
                {
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "symbol": symbol,
                    "name": name,
                    "score": score,
                    "direction": direction,
                    "side": side,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "return": trade_return,
                    "outcome": outcome_label(trade_return),
                    "entry_reasons": reasons,
                    "outcome_reasons": outcome_reasons(trade_return, direction, row, context),
                    "holding_days": holding_days,
                    "entry_price_rule": "next_xtks_session_open",
                    "exit_price_rule": "holding_period_close",
                }
            )

    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows).sort_values("signal_date", ascending=False)
    return result if max_trades is None else result.head(max_trades)


def summarize_virtual_trade_feedback(trades: pd.DataFrame) -> dict[str, dict]:
    if trades.empty:
        return {}

    feedback: dict[str, dict] = {}
    for symbol, group in trades.groupby("symbol"):
        returns = pd.to_numeric(group["return"], errors="coerce").dropna()
        if returns.empty:
            continue
        feedback[symbol] = {
            "trades": int(len(returns)),
            "win_rate": float((returns > 0).mean()),
            "average_return": float(returns.mean()),
            "large_move_rate": float((returns.abs() >= 0.05).mean()),
        }
    return feedback


def outcome_label(value: float) -> str:
    if value >= 0.05:
        return "大きく上昇"
    if value > 0:
        return "上昇"
    if value <= -0.05:
        return "大きく下落"
    return "下落"


def outcome_reasons(trade_return: float, direction: str, row: pd.Series, context: dict) -> list[str]:
    us_return = context["us_signal"].get("average_return")
    if pd.notna(us_return):
        reasons = [f"{float(us_return):.2%} の米国指数平均変動を背景に判定しました"]
    else:
        reasons = ["米国指数平均変動は不足データのため評価できませんでした"]
    if trade_return > 0 and "上方向" in direction:
        reasons.append("想定方向と実際の損益方向が一致しました")
    elif trade_return < 0 and "下方向" in direction:
        reasons.append("下方向の想定で、価格下落が発生しました")
    elif direction != "方向感は未確定":
        reasons.append("想定方向と実際の値動きが一致しませんでした")

    volatility = row.get("volatility_20d")
    if pd.notna(volatility):
        reasons.append(f"シグナル時点の20日ボラティリティは {volatility:.2%} でした")
    return reasons
