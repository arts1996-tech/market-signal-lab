import pandas as pd

from app.analysis.movement_candidates import (
    movement_score,
    us_japan_market_context,
)
from app.analysis.technical import short_term_indicator_frame


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
    for symbol, group in price_frame.groupby("symbol"):
        ordered = group.drop_duplicates("price_time").set_index("price_time").sort_index()
        close = ordered["close"]
        if len(close) < min_observations + holding_days:
            continue
        indicators = short_term_indicator_frame(close)
        name = group["name"].dropna().iloc[-1] if "name" in group and not group["name"].dropna().empty else symbol
        for location in range(min_observations, len(close) - holding_days):
            signal_date = close.index[location]
            row = indicators.loc[signal_date]
            historical_index_prices = (
                index_prices[pd.to_datetime(index_prices["price_time"], utc=True) <= signal_date]
                if not index_prices.empty and "price_time" in index_prices
                else pd.DataFrame()
            )
            context = us_japan_market_context(historical_index_prices)
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
