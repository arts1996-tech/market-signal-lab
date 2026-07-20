import pandas as pd
import numpy as np

from app.analysis.movement_candidates import (
    movement_score,
    us_japan_market_context,
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
    dates = pd.bdate_range("2025-01-06", periods=periods, tz="UTC")
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
    lot_size: int = 100,
) -> dict:
    """Simulate cash/equity for demo or backtest trades without placing orders."""
    if initial_cash <= 0 or not 0 < allocation_rate <= 1 or fee_rate < 0 or lot_size <= 0:
        raise ValueError("invalid virtual account parameters")
    cash = float(initial_cash)
    records: list[dict] = []
    if trades.empty:
        return {"account_name": account_name, "initial_cash": initial_cash, "cash": cash, "equity": cash, "realized_pnl": 0.0, "trades": pd.DataFrame()}
    ordered = trades.sort_values(["signal_date", "exit_date"]).reset_index(drop=True)
    for trade in ordered.to_dict(orient="records"):
        entry = float(trade["entry_price"])
        exit_price = float(trade["exit_price"])
        if entry <= 0 or exit_price <= 0:
            continue
        budget = min(cash, initial_cash * allocation_rate)
        quantity = int(budget // (entry * lot_size)) * lot_size
        if quantity <= 0:
            continue
        direction = str(trade.get("direction", ""))
        multiplier = -1 if "下方向" in direction else 1
        entry_value = entry * quantity
        exit_value = exit_price * quantity
        fees = (entry_value + exit_value) * fee_rate
        pnl = multiplier * (exit_value - entry_value) - fees
        cash += pnl
        records.append({"account_name": account_name, "symbol": trade.get("symbol"), "signal_date": trade.get("signal_date"), "exit_date": trade.get("exit_date"), "quantity": quantity, "entry_price": entry, "exit_price": exit_price, "realized_pnl": pnl, "cash_after": cash, "equity_after": cash})
    ledger = pd.DataFrame(records)
    realized = float(ledger["realized_pnl"].sum()) if not ledger.empty else 0.0
    return {"account_name": account_name, "initial_cash": float(initial_cash), "cash": cash, "equity": cash, "realized_pnl": realized, "unrealized_pnl": 0.0, "trades": ledger}


def build_virtual_trades(
    index_prices: pd.DataFrame,
    japan_prices: pd.DataFrame,
    score_threshold: int = 70,
    holding_days: int = 5,
    min_observations: int = 30,
    max_trades: int = 50,
) -> pd.DataFrame:
    if japan_prices.empty:
        return pd.DataFrame()

    price_frame = japan_prices.copy()
    price_frame["price_time"] = pd.to_datetime(price_frame["price_time"], utc=True).dt.normalize()
    price_frame["close"] = pd.to_numeric(price_frame["close"])

    rows = []
    context_cache = {}
    for symbol, group in price_frame.groupby("symbol"):
        ordered = group.drop_duplicates("price_time").set_index("price_time").sort_index()
        close = ordered["close"]
        if len(close) < min_observations + holding_days:
            continue
        indicators = short_term_indicator_frame(close)
        name = group["name"].dropna().iloc[-1] if "name" in group and not group["name"].dropna().empty else symbol
        start_location = max(min_observations, len(close) - holding_days - 10)
        for location in range(start_location, len(close) - holding_days):
            signal_date = close.index[location]
            row = indicators.loc[signal_date]
            cache_key = pd.Timestamp(signal_date)
            if cache_key not in context_cache:
                historical_index_prices = (
                    index_prices[pd.to_datetime(index_prices["price_time"], utc=True) <= signal_date]
                    if not index_prices.empty and "price_time" in index_prices
                    else pd.DataFrame()
                )
                context_cache[cache_key] = us_japan_market_context(historical_index_prices)
            context = context_cache[cache_key]
            score, direction, reasons = movement_score(row, context["average_correlation"], context["us_signal"])
            if score < score_threshold:
                continue
            exit_date = close.index[location + holding_days]
            entry_price = float(close.iloc[location])
            exit_price = float(close.iloc[location + holding_days])
            trade_return = exit_price / entry_price - 1
            rows.append(
                {
                    "signal_date": signal_date,
                    "exit_date": exit_date,
                    "symbol": symbol,
                    "name": name,
                    "score": score,
                    "direction": direction,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "return": trade_return,
                    "outcome": outcome_label(trade_return),
                    "entry_reasons": reasons,
                    "outcome_reasons": outcome_reasons(trade_return, direction, row, context),
                    "holding_days": holding_days,
                }
            )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("signal_date", ascending=False).head(max_trades)


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
