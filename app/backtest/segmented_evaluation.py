"""Point-in-time regime and attribute summaries for completed virtual trades."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import pandas as pd

from app.analysis.market_calendar import latest_contiguous_exchange_observations
from app.backtest.audit import frame_hash, json_value, stable_payload_hash


SEGMENTED_EVALUATION_VERSION = "segmented-trade-evaluation-v1"
CLOSED_ACTIONS = {"利益確定", "損切り", "保有期限決済", "上場廃止評価"}
SEGMENT_DIMENSIONS = (
    "market_direction",
    "volatility_regime",
    "fx_regime",
    "sector",
    "liquidity_band",
    "score_band",
)


@dataclass(frozen=True)
class SegmentedEvaluationPolicy:
    version: str = SEGMENTED_EVALUATION_VERSION
    lookback_sessions: int = 20
    market_up_threshold: float = 0.05
    market_down_threshold: float = -0.05
    high_daily_volatility_threshold: float = 0.015
    yen_weakening_threshold: float = 0.02
    yen_strengthening_threshold: float = -0.02
    high_turnover_threshold: float = 1_000_000_000.0
    medium_turnover_threshold: float = 250_000_000.0
    low_turnover_threshold: float = 50_000_000.0
    minimum_assessment_trades: int = 30
    context_availability_basis: str = "historical_session_close_proxy"

    def __post_init__(self) -> None:
        if self.lookback_sessions < 2:
            raise ValueError("lookback_sessions must be at least 2")
        if not self.market_down_threshold < 0 < self.market_up_threshold:
            raise ValueError("market direction thresholds must straddle zero")
        if self.high_daily_volatility_threshold <= 0:
            raise ValueError("volatility threshold must be positive")
        if not self.yen_strengthening_threshold < 0 < self.yen_weakening_threshold:
            raise ValueError("FX thresholds must straddle zero")
        if not (
            self.high_turnover_threshold
            > self.medium_turnover_threshold
            > self.low_turnover_threshold
            > 0
        ):
            raise ValueError("turnover thresholds must be positive and descending")
        if self.minimum_assessment_trades < 2:
            raise ValueError("minimum_assessment_trades must be at least 2")


def _wilson_interval_95(wins: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.96
    rate = wins / total
    denominator = 1 + z**2 / total
    centre = (rate + z**2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2))
        / denominator
    )
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def _mean_interval_95(values: pd.Series) -> list[float] | None:
    usable = pd.to_numeric(values, errors="coerce").dropna()
    if len(usable) < 2:
        return None
    mean = float(usable.mean())
    margin = 1.96 * float(usable.std(ddof=1)) / math.sqrt(len(usable))
    return [mean - margin, mean + margin]


def _historical_series(
    index_prices: pd.DataFrame,
    symbol: str,
    as_of: pd.Timestamp,
) -> pd.Series:
    if index_prices.empty or not {"symbol", "price_time", "close"}.issubset(
        index_prices.columns
    ):
        return pd.Series(dtype=float)
    frame = index_prices[index_prices["symbol"] == symbol].copy()
    if frame.empty:
        return pd.Series(dtype=float)
    frame["price_time"] = pd.to_datetime(
        frame["price_time"], utc=True, errors="coerce"
    ).dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame[
        frame["price_time"].notna()
        & frame["close"].notna()
        & (frame["close"] > 0)
        & (frame["price_time"] <= as_of)
    ]
    return (
        frame.drop_duplicates("price_time", keep="last")
        .set_index("price_time")["close"]
        .sort_index()
    )


def _market_regimes(
    index_prices: pd.DataFrame,
    as_of: pd.Timestamp,
    policy: SegmentedEvaluationPolicy,
) -> dict[str, str | float | None]:
    nikkei = _historical_series(index_prices, "NIKKEI225", as_of)
    usd_jpy = _historical_series(index_prices, "DEXJPUS", as_of)
    required = policy.lookback_sessions + 1
    if not nikkei.empty:
        contiguous = latest_contiguous_exchange_observations(nikkei.index, "XTKS")
        nikkei = nikkei.tail(contiguous)
        if nikkei.empty or nikkei.index[-1] != as_of:
            nikkei = pd.Series(dtype=float)
    if not usd_jpy.empty and as_of - usd_jpy.index[-1] > pd.Timedelta(days=4):
        usd_jpy = pd.Series(dtype=float)
    market_return = None
    daily_volatility = None
    if len(nikkei) >= required:
        window = nikkei.tail(required)
        market_return = float(window.iloc[-1] / window.iloc[0] - 1)
        returns = window.pct_change(fill_method=None).dropna()
        if len(returns) == policy.lookback_sessions:
            daily_volatility = float(returns.std(ddof=1))
    if market_return is None:
        market_direction = "data_unavailable"
    elif market_return >= policy.market_up_threshold:
        market_direction = "uptrend"
    elif market_return <= policy.market_down_threshold:
        market_direction = "downtrend"
    else:
        market_direction = "sideways"
    if daily_volatility is None or not math.isfinite(daily_volatility):
        volatility_regime = "data_unavailable"
    elif daily_volatility >= policy.high_daily_volatility_threshold:
        volatility_regime = "high_volatility"
    else:
        volatility_regime = "low_volatility"

    fx_return = None
    if len(usd_jpy) >= required:
        fx_window = usd_jpy.tail(required)
        if fx_window.index[-1] - fx_window.index[0] <= pd.Timedelta(days=35):
            fx_return = float(fx_window.iloc[-1] / fx_window.iloc[0] - 1)
    if fx_return is None:
        fx_regime = "data_unavailable"
    elif fx_return >= policy.yen_weakening_threshold:
        fx_regime = "yen_weakening"
    elif fx_return <= policy.yen_strengthening_threshold:
        fx_regime = "yen_strengthening"
    else:
        fx_regime = "fx_neutral"
    return {
        "market_direction": market_direction,
        "market_return_20d": market_return,
        "volatility_regime": volatility_regime,
        "market_daily_volatility_20d": daily_volatility,
        "fx_regime": fx_regime,
        "usd_jpy_return_20d": fx_return,
    }


def _liquidity_band(
    value: object, policy: SegmentedEvaluationPolicy
) -> str:
    turnover = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(turnover) or float(turnover) <= 0:
        return "data_unavailable"
    if float(turnover) >= policy.high_turnover_threshold:
        return "high"
    if float(turnover) >= policy.medium_turnover_threshold:
        return "medium"
    if float(turnover) >= policy.low_turnover_threshold:
        return "low"
    return "below_minimum"


def _score_band(value: object) -> str:
    score = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(score):
        return "data_unavailable"
    if float(score) >= 90:
        return "90_100"
    if float(score) >= 80:
        return "80_89"
    if float(score) >= 70:
        return "70_79"
    return "below_70"


def classify_completed_trades(
    simulation_result: dict,
    index_prices: pd.DataFrame,
    *,
    policy: SegmentedEvaluationPolicy | None = None,
    validation_window: int | None = None,
) -> pd.DataFrame:
    """Classify completed trades using context available by their decision date."""

    policy = policy or SegmentedEvaluationPolicy()
    transactions = simulation_result.get("transactions")
    if transactions is None or transactions.empty:
        return pd.DataFrame()
    closed = transactions[transactions["action"].isin(CLOSED_ACTIONS)].copy()
    if closed.empty:
        return pd.DataFrame()
    required = {
        "symbol",
        "date",
        "decision_as_of",
        "realized_pnl",
        "trade_return",
    }
    missing = required.difference(closed.columns)
    if missing:
        raise ValueError(f"completed transactions missing columns: {sorted(missing)}")
    closed["date"] = pd.to_datetime(
        closed["date"], utc=True, errors="coerce"
    ).dt.normalize()
    closed["decision_as_of"] = pd.to_datetime(
        closed["decision_as_of"], utc=True, errors="coerce"
    ).dt.normalize()
    if closed[["date", "decision_as_of"]].isna().any().any():
        raise ValueError("completed transactions require valid decision and exit dates")
    rows = []
    run_id = (simulation_result.get("manifest") or {}).get("run_id")
    for trade in closed.to_dict(orient="records"):
        as_of = pd.Timestamp(trade["decision_as_of"])
        regimes = _market_regimes(index_prices, as_of, policy)
        sector = str(trade.get("sector") or "unknown").strip() or "unknown"
        row = {
            "trade_id": stable_payload_hash(
                {
                    "run_id": run_id,
                    "symbol": trade["symbol"],
                    "decision_as_of": as_of,
                    "exit_date": trade["date"],
                    "action": trade["action"],
                }
            ),
            "run_id": run_id,
            "validation_window": validation_window,
            "symbol": str(trade["symbol"]),
            "decision_as_of": as_of,
            "exit_date": trade["date"],
            "realized_pnl": float(trade["realized_pnl"]),
            "trade_return": float(trade["trade_return"]),
            "score": trade.get("score"),
            "sector": sector,
            "previous_turnover": trade.get("previous_turnover"),
            "liquidity_band": _liquidity_band(
                trade.get("previous_turnover"), policy
            ),
            "score_band": _score_band(trade.get("score")),
            "context_availability_basis": policy.context_availability_basis,
            **regimes,
        }
        rows.append(row)
    result = pd.DataFrame(rows)
    if result["trade_id"].duplicated().any():
        raise ValueError("completed trade identity must be unique")
    return result.sort_values(["decision_as_of", "symbol"]).reset_index(drop=True)


def _summary_row(
    trades: pd.DataFrame,
    *,
    dimension: str,
    label: str,
    policy: SegmentedEvaluationPolicy,
) -> dict:
    returns = pd.to_numeric(trades["trade_return"], errors="coerce").dropna()
    pnls = pd.to_numeric(trades["realized_pnl"], errors="coerce").dropna()
    wins = int((returns > 0).sum())
    count = int(len(returns))
    mean_interval = _mean_interval_95(returns)
    if count < policy.minimum_assessment_trades:
        assessment = "not_assessed_small_sample"
        sample_status = "insufficient_sample"
    else:
        sample_status = "assessment_allowed"
        if mean_interval is not None and mean_interval[0] > 0:
            assessment = "positive_observed"
        elif mean_interval is not None and mean_interval[1] < 0:
            assessment = "negative_observed"
        else:
            assessment = "inconclusive"
    return {
        "evaluation_version": policy.version,
        "segment_dimension": dimension,
        "segment_label": str(label),
        "trade_count": count,
        "win_count": wins,
        "win_rate": None if count == 0 else wins / count,
        "win_rate_ci95": _wilson_interval_95(wins, count),
        "average_trade_return": None if count == 0 else float(returns.mean()),
        "average_trade_return_ci95": mean_interval,
        "total_realized_pnl": float(pnls.sum()),
        "minimum_assessment_trades": policy.minimum_assessment_trades,
        "sample_status": sample_status,
        "performance_assessment": assessment,
    }


def summarize_segmented_trades(
    trades: pd.DataFrame,
    *,
    policy: SegmentedEvaluationPolicy | None = None,
) -> dict:
    """Return versioned summaries without judging groups below the sample gate."""

    policy = policy or SegmentedEvaluationPolicy()
    if trades.empty:
        return {
            "version": policy.version,
            "policy": json_value(asdict(policy)),
            "completed_trades": 0,
            "input_hash": frame_hash(trades),
            "summaries": [],
            "warnings": ["no_closed_trades_for_segmented_evaluation"],
        }
    missing = {"trade_id", "trade_return", "realized_pnl"}.difference(
        trades.columns
    )
    if missing:
        raise ValueError(f"segmented trades missing columns: {sorted(missing)}")
    if trades["trade_id"].duplicated().any():
        raise ValueError("segmented trade IDs must be unique across validation windows")
    rows = [
        _summary_row(
            trades,
            dimension="overall",
            label="all_closed_trades",
            policy=policy,
        )
    ]
    for dimension in SEGMENT_DIMENSIONS:
        if dimension not in trades:
            continue
        for label, group in trades.groupby(dimension, dropna=False, sort=True):
            normalized_label = "data_unavailable" if pd.isna(label) else str(label)
            rows.append(
                _summary_row(
                    group,
                    dimension=dimension,
                    label=normalized_label,
                    policy=policy,
                )
            )
    small_sample_count = sum(
        row["sample_status"] == "insufficient_sample" for row in rows
    )
    return {
        "version": policy.version,
        "policy": json_value(asdict(policy)),
        "completed_trades": int(len(trades)),
        "input_hash": frame_hash(trades),
        "summaries": json_value(rows),
        "warnings": (
            ["small_sample_segments_are_not_performance_assessed"]
            if small_sample_count
            else []
        ),
    }
