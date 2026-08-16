from dataclasses import dataclass

import pandas as pd

from app.backtest.validation_registry import claim_validation_window


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def walk_forward_windows(
    sessions: pd.DatetimeIndex,
    *,
    minimum_train_sessions: int,
    test_sessions: int,
    step_sessions: int | None = None,
) -> list[WalkForwardWindow]:
    ordered = pd.DatetimeIndex(pd.to_datetime(sessions, utc=True).normalize()).drop_duplicates().sort_values()
    if minimum_train_sessions <= 0 or test_sessions <= 0:
        raise ValueError("training and test lengths must be positive")
    step = step_sessions or test_sessions
    if step <= 0:
        raise ValueError("step_sessions must be positive")
    windows = []
    train_end_location = minimum_train_sessions - 1
    while train_end_location + 1 < len(ordered):
        test_start_location = train_end_location + 1
        test_end_location = min(test_start_location + test_sessions - 1, len(ordered) - 1)
        if test_end_location < test_start_location:
            break
        windows.append(
            WalkForwardWindow(
                train_start=ordered[0],
                train_end=ordered[train_end_location],
                test_start=ordered[test_start_location],
                test_end=ordered[test_end_location],
            )
        )
        train_end_location += step
    return windows


def evaluate_frozen_strategy_walk_forward(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    simulator,
    *,
    minimum_train_sessions: int,
    test_sessions: int,
    validation_registry_path=None,
    strategy_version: str | None = None,
    rule_hash: str | None = None,
    evaluation_track: str = "default",
) -> pd.DataFrame:
    """Evaluate a frozen rule only on each unseen test window.

    The simulator callable receives ``test_signals`` and ``prices_as_of_test``.
    Training rows are never passed as test signals.
    """

    if prices.empty:
        return pd.DataFrame()
    frame = prices.copy()
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True).dt.normalize()
    sessions = pd.DatetimeIndex(sorted(frame["price_time"].unique()))
    windows = walk_forward_windows(
        sessions,
        minimum_train_sessions=minimum_train_sessions,
        test_sessions=test_sessions,
    )
    signal_frame = signals.copy()
    if not signal_frame.empty:
        signal_frame["signal_date"] = pd.to_datetime(signal_frame["signal_date"], utc=True).dt.normalize()
    rows = []
    for index, window in enumerate(windows):
        validation_claim = None
        if validation_registry_path is not None:
            if not strategy_version or not rule_hash:
                raise ValueError(
                    "strategy_version and rule_hash are required when claiming validation windows"
                )
            validation_claim = claim_validation_window(
                validation_registry_path,
                strategy_version=strategy_version,
                rule_hash=rule_hash,
                evaluation_track=evaluation_track,
                test_start=window.test_start,
                test_end=window.test_end,
            )
        test_signals = signal_frame[
            (signal_frame["signal_date"] >= window.test_start)
            & (signal_frame["signal_date"] <= window.test_end)
        ].copy()
        prices_as_of_test = frame[frame["price_time"] <= window.test_end].copy()
        result = simulator(test_signals, prices_as_of_test)
        metrics = result.get("metrics", {})
        rows.append(
            {
                "window": index + 1,
                "train_start": window.train_start,
                "train_end": window.train_end,
                "test_start": window.test_start,
                "test_end": window.test_end,
                "test_signals": len(test_signals),
                "total_return": metrics.get("total_return"),
                "maximum_drawdown": metrics.get("maximum_drawdown"),
                "closed_trades": metrics.get("closed_trades"),
                "benchmark_return": metrics.get("benchmark_return"),
                "excess_return": metrics.get("excess_return"),
                "run_id": result.get("manifest", {}).get("run_id"),
                "validation_claim_id": (
                    None if validation_claim is None else validation_claim["claim_id"]
                ),
            }
        )
    return pd.DataFrame(rows)
