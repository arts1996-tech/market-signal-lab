import pandas as pd

from app.analysis.movement_candidates import apply_virtual_trade_feedback, build_movement_candidates
from app.analysis.virtual_trading import build_virtual_trades, summarize_virtual_trade_feedback


def _price_rows(symbol: str, values: list[float], name: str | None = None, source: str = "jquants") -> list[dict]:
    dates = pd.date_range("2026-01-01", periods=len(values), freq="B", tz="UTC")
    return [
        {
            "symbol": symbol,
            "name": name or symbol,
            "price_time": date,
            "close": value,
            "source": source,
            "fetched_at": date,
        }
        for date, value in zip(dates, values, strict=True)
    ]


def test_build_movement_candidates_returns_insufficient_rows():
    japan_prices = pd.DataFrame(_price_rows("86970", [100, 101, 102]))

    result = build_movement_candidates(pd.DataFrame(), japan_prices, min_observations=30)

    assert result["candidates"].empty
    assert result["insufficient"].loc[0, "symbol"] == "86970"


def test_apply_virtual_trade_feedback_adjusts_score():
    adjusted, reason, feedback_score = apply_virtual_trade_feedback(
        70,
        {"trades": 5, "win_rate": 0.8, "average_return": 0.03, "large_move_rate": 0.5},
    )

    assert adjusted > 70
    assert "勝率" in reason
    assert feedback_score > 0


def test_virtual_trade_feedback_loop_builds_summary():
    index_prices = pd.DataFrame(
        _price_rows("NASDAQCOM", [100 + i for i in range(80)], source="fred")
        + _price_rows("DJIA", [200 + i for i in range(80)], source="fred")
        + _price_rows("SP500", [300 + i for i in range(80)], source="fred")
        + _price_rows("NIKKEI225", [400 + i for i in range(80)], source="fred")
    )
    japan_prices = pd.DataFrame(_price_rows("86970", [100 + i * 1.8 for i in range(80)]))

    trades = build_virtual_trades(
        index_prices,
        japan_prices,
        score_threshold=50,
        holding_days=5,
        min_observations=30,
    )
    feedback = summarize_virtual_trade_feedback(trades)

    assert not trades.empty
    assert "86970" in feedback
    assert feedback["86970"]["trades"] > 0
