from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.backtest.ohlc import (
    MarketImpactAssumptions,
    PortfolioRiskRules,
    simulate_ohlc_portfolio,
)
from app.backtest.portfolio import ExecutionAssumptions
from app.backtest.audit import stable_payload_hash
from app.backtest.validation import evaluate_frozen_strategy_walk_forward
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


def _demo_benchmarks(prices: pd.DataFrame) -> tuple[pd.Series, dict[str, pd.Series]]:
    closes = prices.pivot_table(
        index="price_time", columns="symbol", values="close", aggfunc="last"
    ).sort_index()
    primary = closes["DEMOETF"].dropna()
    normalized = closes.divide(closes.iloc[0]).dropna(how="all")
    equal_weight = normalized.mean(axis=1) * 100.0
    return primary, {"equal_weight_simple_hold": equal_weight}


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
        ("DEMOJP1", "検証用テクノロジー", "technology", 1200.0, 1.25, 0.012),
        ("DEMOJP2", "検証用製造業", "manufacturing", 2800.0, 0.85, 0.010),
        ("DEMOJP3", "検証用小売業", "retail", 1750.0, 0.55, 0.014),
        ("DEMOETF", "検証用日本ETF", "diversified_etf", 2200.0, 0.70, 0.007),
    )
    records = []
    for index, (symbol, name, sector, start, beta, noise) in enumerate(definitions):
        returns = market * beta + rng.normal(0.0001 * (index - 1), noise, periods)
        closes = start * np.cumprod(1 + returns)
        overnight = rng.normal(0, noise * 0.25, periods)
        opens = np.r_[start, closes[:-1]] * (1 + overnight)
        intraday_range = np.abs(rng.normal(noise * 0.8, noise * 0.25, periods))
        highs = np.maximum(opens, closes) * (1 + intraday_range / 2)
        lows = np.minimum(opens, closes) * (1 - intraday_range / 2)
        volumes = rng.integers(80_000, 1_200_000, periods)
        records.extend(
            {
                "price_time": date,
                "symbol": symbol,
                "name": name,
                "sector": sector,
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": int(volume),
                "source": "demo",
                "price_basis": "synthetic_unadjusted",
                "synthetic": True,
            }
            for date, open_price, high, low, close, volume in zip(
                dates, opens, highs, lows, closes, volumes, strict=True
            )
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
    validation_registry_path=None,
) -> dict:
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(prices["price_time"], utc=True).unique()))
    signal_rows: list[dict] = []
    latest_signals: list[dict] = []
    sectors = prices.drop_duplicates("symbol").set_index("symbol")["sector"].to_dict()
    for location in range(20, len(dates)):
        decision_date = dates[location - 1]
        entry_date = dates[location]
        history = prices[pd.to_datetime(prices["price_time"], utc=True) <= decision_date]
        latest_signals = _rank_signals(history, news, decision_date)
        for signal in latest_signals:
            if signal["score"] < rule.minimum_score:
                continue
            signal_rows.append(
                {
                    **signal,
                    "signal_date": decision_date,
                    "entry_date": entry_date,
                    "side": "long",
                    "sector": sectors.get(signal["symbol"], "unknown"),
                    "minimum_score": rule.minimum_score,
                    "stop_loss": rule.stop_loss,
                    "take_profit": rule.take_profit,
                    "maximum_holding_days": rule.maximum_holding_days,
                    "counterarguments": [
                        "合成データであり実市場の板・ニュース・企業行動を再現しません"
                    ],
                }
            )
    signals = pd.DataFrame(signal_rows)
    assumptions = ExecutionAssumptions(
        fee_rate=fee_rate,
        spread_rate=spread_rate,
        tax_rate=0.0,
        lot_size=lot_size,
        maximum_positions=rule.maximum_positions,
        maximum_position_rate=rule.maximum_position_rate,
    )
    primary_benchmark, comparison_benchmarks = _demo_benchmarks(prices)
    result = simulate_ohlc_portfolio(
        signals,
        prices,
        initial_cash=initial_cash,
        account_name=rule.name,
        assumptions=assumptions,
        market_impact=MarketImpactAssumptions(
            require_volume=True,
            minimum_previous_turnover=50_000_000,
            use_turnover_cost_model=True,
        ),
        risk_rules=PortfolioRiskRules(
            maximum_sector_rate=0.50,
            maximum_position_correlation=0.85,
        ),
        benchmark=primary_benchmark,
        benchmarks=comparison_benchmarks,
        input_data_version="deterministic-synthetic-demo-v2",
    )
    walk_forward = evaluate_frozen_strategy_walk_forward(
        signals,
        prices,
        lambda test_signals, prices_as_of_test: simulate_ohlc_portfolio(
            test_signals,
            prices_as_of_test,
            initial_cash=initial_cash,
            account_name=rule.name,
            assumptions=assumptions,
            market_impact=MarketImpactAssumptions(
                require_volume=True,
                minimum_previous_turnover=50_000_000,
                use_turnover_cost_model=True,
            ),
            risk_rules=PortfolioRiskRules(
                maximum_sector_rate=0.50,
                maximum_position_correlation=0.85,
            ),
            benchmark=_demo_benchmarks(prices_as_of_test)[0],
            benchmarks=_demo_benchmarks(prices_as_of_test)[1],
            input_data_version="deterministic-synthetic-demo-v2",
        ),
        minimum_train_sessions=60,
        test_sessions=20,
        validation_registry_path=validation_registry_path,
        strategy_version=result["manifest"]["strategy_version"],
        rule_hash=stable_payload_hash(
            {
                "account_name": rule.name,
                "strategy_version": result["manifest"]["strategy_version"],
                "execution_version": result["manifest"]["execution_version"],
                "assumptions": result["manifest"]["assumptions"],
                "risk_rules": result["manifest"]["risk_rules"],
                "account_rule": rule,
            }
        ),
        evaluation_track=rule.name,
    )
    result.update(
        {
            "label": rule.label,
            "maximum_drawdown": result["metrics"]["maximum_drawdown"],
            "latest_signals": pd.DataFrame(latest_signals),
            "signals": signals,
            "walk_forward": walk_forward,
        }
    )
    return result


def run_demo_portfolio_environment(
    periods: int = 180,
    initial_cash: float = 2_500_000,
    fee_rate: float = 0.001,
    spread_rate: float = 0.001,
    lot_size: int = 100,
    validation_registry_path=None,
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
        rule.name: _run_account(
            prices,
            news,
            rule,
            initial_cash,
            fee_rate,
            spread_rate,
            lot_size,
            validation_registry_path,
        )
        for rule in DEMO_ACCOUNT_RULES
    }
    return {
        "mode": "demo_only",
        "warning": "合成価格・合成ニュースによる検証環境です。実績・予測・投資判断には使用できません。",
        "assumptions": {
            "initial_cash_each": initial_cash,
            "fee_rate": fee_rate,
            "spread_rate": spread_rate,
            "execution_cost_model": (
                "前営業日売買代金5千万円以上を対象とする3段階の代理スプレッド・スリッページ"
            ),
            "lot_size": lot_size,
            "tax_rate": 0.0,
            "currency": "JPY",
            "execution_rule": "前営業日までの情報で判断し、次営業日の始値で仮想約定",
            "maximum_positions": 2,
            "maximum_position_rate": 0.30,
            "maximum_volume_participation": 0.10,
            "partial_fill": True,
            "simultaneous_hit_policy": "損切りを先に成立したものとして扱う",
            "strategy_version": "phase4-long-only-v0.3",
            "price_source": "deterministic_synthetic_demo",
            "news_source": "deterministic_synthetic_demo_scenario",
            "open_position_valuation": "当日終値。未実現損益には将来の決済手数料を含めない",
        },
        "prices": prices,
        "news": news,
        "accounts": accounts,
    }
