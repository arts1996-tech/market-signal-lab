from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.providers.news import DemoNewsProvider


@dataclass(frozen=True)
class DemoAccountRule:
    name: str
    label: str
    minimum_score: float
    take_profit: float
    stop_loss: float
    maximum_holding_days: int
    maximum_positions: int = 2
    maximum_position_rate: float = 0.30


DEMO_ACCOUNT_RULES = (
    DemoAccountRule("short_term", "短期", 56.0, 0.08, -0.05, 10),
    DemoAccountRule("mid_term", "中期", 53.0, 0.18, -0.10, 60),
)


def generate_demo_portfolio_prices(periods: int = 180) -> pd.DataFrame:
    if periods < 80:
        raise ValueError("periods must be at least 80")
    rng = np.random.default_rng(20260720)
    sessions = exchange_calendar("XTKS").sessions_in_range("2025-01-01", "2026-12-31")
    if periods > len(sessions):
        raise ValueError("periods exceeds available demo exchange sessions")
    dates = pd.DatetimeIndex(pd.to_datetime(sessions[:periods], utc=True))
    market = rng.normal(0.00035, 0.011, periods)
    definitions = (
        ("DEMOJP1", "検証用テクノロジー", 1200.0, 1.25, 0.012),
        ("DEMOJP2", "検証用製造業", 2800.0, 0.85, 0.010),
        ("DEMOJP3", "検証用小売業", 1750.0, 0.55, 0.014),
        ("DEMOETF", "検証用日本ETF", 2200.0, 0.70, 0.007),
    )
    records = []
    for index, (symbol, name, start, beta, noise) in enumerate(definitions):
        returns = market * beta + rng.normal(0.0001 * (index - 1), noise, periods)
        values = start * np.cumprod(1 + returns)
        records.extend(
            {
                "price_time": date,
                "symbol": symbol,
                "name": name,
                "close": float(value),
                "source": "demo",
                "synthetic": True,
            }
            for date, value in zip(dates, values, strict=True)
        )
    return pd.DataFrame(records)


def _news_context(news: pd.DataFrame, symbol: str, as_of: pd.Timestamp, days: int = 5) -> tuple[float, list[str]]:
    if news.empty:
        return 0.0, []
    start = as_of - pd.Timedelta(days=days * 2)
    available = news[
        (news["symbol"] == symbol)
        & (pd.to_datetime(news["published_at"], utc=True) <= as_of)
        & (pd.to_datetime(news["published_at"], utc=True) >= start)
    ].tail(days)
    if available.empty:
        return 0.0, []
    score = float((available["sentiment"] * available["relevance"]).mean())
    return score, available["headline"].astype(str).tolist()


def _rank_signals(history: pd.DataFrame, news: pd.DataFrame, as_of: pd.Timestamp) -> list[dict]:
    signals = []
    for symbol, group in history.groupby("symbol"):
        ordered = group.sort_values("price_time").drop_duplicates("price_time")
        if len(ordered) < 20:
            continue
        close = ordered["close"].astype(float)
        momentum_5 = float(close.iloc[-1] / close.iloc[-6] - 1)
        trend_20 = float(close.iloc[-1] / close.tail(20).mean() - 1)
        news_score, headlines = _news_context(news, symbol, as_of)
        score = float(np.clip(50 + momentum_5 * 220 + trend_20 * 160 + news_score * 12, 0, 100))
        reasons = [f"5日騰落率 {momentum_5:+.1%}", f"20日平均乖離 {trend_20:+.1%}"]
        if headlines:
            reasons.append(f"検証用ニュース評価 {news_score:+.2f}")
        signals.append(
            {
                "symbol": symbol,
                "name": str(ordered.iloc[-1]["name"]),
                "score": score,
                "reasons": reasons,
                "news_headlines": headlines,
            }
        )
    return sorted(signals, key=lambda item: item["score"], reverse=True)


def _run_account(
    prices: pd.DataFrame,
    news: pd.DataFrame,
    rule: DemoAccountRule,
    initial_cash: float,
    fee_rate: float,
    spread_rate: float,
    lot_size: int,
) -> dict:
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(prices["price_time"], utc=True).unique()))
    cash = float(initial_cash)
    realized_pnl = 0.0
    positions: dict[str, dict] = {}
    transactions: list[dict] = []
    snapshots: list[dict] = []
    high_watermark = float(initial_cash)
    latest_signals: list[dict] = []

    price_table = prices.pivot(index="price_time", columns="symbol", values="close").sort_index()
    names = prices.drop_duplicates("symbol").set_index("symbol")["name"].to_dict()

    for location in range(20, len(dates)):
        execution_date = dates[location]
        decision_date = dates[location - 1]
        history = prices[pd.to_datetime(prices["price_time"], utc=True) <= decision_date]
        latest_signals = _rank_signals(history, news, decision_date)
        signal_by_symbol = {item["symbol"]: item for item in latest_signals}

        for symbol, position in list(positions.items()):
            decision_price = float(price_table.loc[decision_date, symbol])
            return_since_entry = decision_price / position["entry_price"] - 1
            held_days = location - position["entry_location"]
            signal = signal_by_symbol.get(symbol, {})
            news_headlines = signal.get("news_headlines", [])
            exit_reason = None
            if return_since_entry <= rule.stop_loss:
                exit_reason = "損切り条件成立"
            elif return_since_entry >= rule.take_profit:
                exit_reason = "利益確定条件成立"
            elif held_days >= rule.maximum_holding_days:
                exit_reason = "最大保有期間到達"
            elif signal.get("score", 50) < 40 and news_headlines:
                exit_reason = "ニュース・価格条件の悪化"
            if not exit_reason:
                continue
            market_price = float(price_table.loc[execution_date, symbol])
            execution_price = market_price * (1 - spread_rate / 2)
            gross = execution_price * position["quantity"]
            fee = gross * fee_rate
            proceeds = gross - fee
            pnl = proceeds - position["cost"]
            cash += proceeds
            realized_pnl += pnl
            action = {
                "利益確定条件成立": "利益確定",
                "損切り条件成立": "損切り",
                "最大保有期間到達": "保有期限決済",
                "ニュース・価格条件の悪化": "条件悪化決済",
            }[exit_reason]
            transactions.append(
                {
                    "account": rule.name,
                    "date": execution_date,
                    "action": action,
                    "symbol": symbol,
                    "name": names[symbol],
                    "quantity": position["quantity"],
                    "execution_price": execution_price,
                    "amount": proceeds,
                    "realized_pnl": pnl,
                    "reason": exit_reason,
                    "decision_as_of": decision_date,
                }
            )
            del positions[symbol]

        market_value_before_entry = sum(
            float(price_table.loc[execution_date, symbol]) * position["quantity"]
            for symbol, position in positions.items()
        )
        equity_before_entry = cash + market_value_before_entry
        available_slots = rule.maximum_positions - len(positions)
        for signal in latest_signals:
            if available_slots <= 0 or signal["score"] < rule.minimum_score:
                break
            symbol = signal["symbol"]
            if symbol in positions or pd.isna(price_table.loc[execution_date, symbol]):
                continue
            market_price = float(price_table.loc[execution_date, symbol])
            execution_price = market_price * (1 + spread_rate / 2)
            budget = min(cash, equity_before_entry * rule.maximum_position_rate)
            quantity = int(budget // (execution_price * lot_size * (1 + fee_rate))) * lot_size
            if quantity <= 0:
                continue
            gross = execution_price * quantity
            fee = gross * fee_rate
            cost = gross + fee
            if cost > cash:
                continue
            cash -= cost
            positions[symbol] = {
                "quantity": quantity,
                "entry_price": execution_price,
                "entry_date": execution_date,
                "entry_location": location,
                "cost": cost,
                "reasons": signal["reasons"],
            }
            transactions.append(
                {
                    "account": rule.name,
                    "date": execution_date,
                    "action": "仮想エントリー",
                    "symbol": symbol,
                    "name": names[symbol],
                    "quantity": quantity,
                    "execution_price": execution_price,
                    "amount": cost,
                    "realized_pnl": 0.0,
                    "reason": " / ".join(signal["reasons"]),
                    "decision_as_of": decision_date,
                }
            )
            available_slots -= 1

        market_value = sum(
            float(price_table.loc[execution_date, symbol]) * position["quantity"]
            for symbol, position in positions.items()
        )
        cost_basis = sum(position["cost"] for position in positions.values())
        unrealized_pnl = market_value - cost_basis
        equity = cash + market_value
        high_watermark = max(high_watermark, equity)
        snapshots.append(
            {
                "date": execution_date,
                "cash": cash,
                "market_value": market_value,
                "equity": equity,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "drawdown": equity / high_watermark - 1,
            }
        )

    snapshot_frame = pd.DataFrame(snapshots)
    position_records = []
    last_date = dates[-1]
    for symbol, position in positions.items():
        last_price = float(price_table.loc[last_date, symbol])
        position_records.append(
            {
                "symbol": symbol,
                "name": names[symbol],
                "quantity": position["quantity"],
                "entry_date": position["entry_date"],
                "entry_price": position["entry_price"],
                "current_price": last_price,
                "market_value": last_price * position["quantity"],
                "unrealized_pnl": last_price * position["quantity"] - position["cost"],
                "entry_reasons": position["reasons"],
            }
        )
    return {
        "account_name": rule.name,
        "label": rule.label,
        "initial_cash": initial_cash,
        "cash": float(snapshot_frame.iloc[-1]["cash"]),
        "equity": float(snapshot_frame.iloc[-1]["equity"]),
        "realized_pnl": float(snapshot_frame.iloc[-1]["realized_pnl"]),
        "unrealized_pnl": float(snapshot_frame.iloc[-1]["unrealized_pnl"]),
        "maximum_drawdown": float(snapshot_frame["drawdown"].min()),
        "positions": pd.DataFrame(position_records),
        "transactions": pd.DataFrame(transactions),
        "snapshots": snapshot_frame,
        "latest_signals": pd.DataFrame(latest_signals),
    }


def run_demo_portfolio_environment(
    periods: int = 180,
    initial_cash: float = 2_500_000,
    fee_rate: float = 0.001,
    spread_rate: float = 0.001,
    lot_size: int = 100,
) -> dict:
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if fee_rate < 0 or spread_rate < 0:
        raise ValueError("fee_rate and spread_rate must be non-negative")
    if lot_size <= 0:
        raise ValueError("lot_size must be positive")
    prices = generate_demo_portfolio_prices(periods)
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(prices["price_time"], utc=True).unique()))
    symbols = prices["symbol"].drop_duplicates().tolist()
    news = DemoNewsProvider().build(dates, symbols)
    accounts = {
        rule.name: _run_account(prices, news, rule, initial_cash, fee_rate, spread_rate, lot_size)
        for rule in DEMO_ACCOUNT_RULES
    }
    return {
        "mode": "demo_only",
        "warning": "合成価格・合成ニュースによる検証環境です。実績・予測・投資判断には使用できません。",
        "assumptions": {
            "initial_cash_each": initial_cash,
            "fee_rate": fee_rate,
            "spread_rate": spread_rate,
            "lot_size": lot_size,
            "tax_rate": 0.0,
            "currency": "JPY",
            "execution_rule": "前営業日までの情報で判断し、次営業日の終値で仮想約定",
            "maximum_positions": 2,
            "maximum_position_rate": 0.30,
            "price_source": "deterministic_synthetic_demo",
            "news_source": "deterministic_synthetic_demo_scenario",
            "open_position_valuation": "当日終値。未実現損益には将来の決済手数料を含めない",
        },
        "prices": prices,
        "news": news,
        "accounts": accounts,
    }
