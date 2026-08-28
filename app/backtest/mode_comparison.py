"""Comparable cash/margin result summaries without performance-based selection."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.analysis.mode_selection import AutoSelectDecision
from app.analysis.trade_modes import TradeMode
from app.backtest.audit import json_value, stable_payload_hash


MODE_COMPARISON_VERSION = "mode-backtest-comparison-v1"


class ModeBacktestSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: TradeMode
    status: str
    run_id: str | None
    total_return: float | None
    maximum_drawdown: float | None
    closed_trades: int = Field(ge=0)
    win_rate: float | None = Field(default=None, ge=0, le=1)
    reported_cost: float | None = Field(default=None, ge=0)
    forced_liquidations: int = Field(default=0, ge=0)
    unfilled_or_rejected: int = Field(default=0, ge=0)
    benchmark_return: float | None = None
    excess_return: float | None = None
    quality_warnings: tuple[str, ...] = ()
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModeBacktestComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = MODE_COMPARISON_VERSION
    summaries: tuple[ModeBacktestSummary, ...]
    auto_select_decisions: tuple[AutoSelectDecision, ...]
    auto_select_series_status: str
    auto_select_series_reason_codes: tuple[str, ...]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    real_order_sent: bool = False


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(converted):
        return None
    return converted


def _frame_length(value: Any) -> int:
    if isinstance(value, pd.DataFrame):
        return len(value)
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


def _cash_reported_cost(result: dict) -> float | None:
    transactions = result.get("transactions")
    if not isinstance(transactions, pd.DataFrame) or transactions.empty:
        return 0.0
    cost_columns = [
        column
        for column in ("fee", "fx_conversion_cost", "tax")
        if column in transactions
    ]
    if not cost_columns:
        return None
    return float(
        sum(
            pd.to_numeric(transactions[column], errors="coerce").fillna(0).sum()
            for column in cost_columns
        )
    )


def _summarize_cash(result: dict) -> ModeBacktestSummary:
    metrics = result.get("metrics") or {}
    manifest = result.get("manifest") or {}
    warnings = tuple(str(item) for item in result.get("quality_warnings") or ())
    payload = {
        "mode": TradeMode.CASH.value,
        "status": result.get("status", "success"),
        "manifest": json_value(manifest),
        "metrics": json_value(metrics),
        "quality_warnings": warnings,
    }
    return ModeBacktestSummary(
        mode=TradeMode.CASH,
        status=str(result.get("status", "success")),
        run_id=manifest.get("run_id"),
        total_return=_safe_float(metrics.get("total_return")),
        maximum_drawdown=_safe_float(metrics.get("maximum_drawdown")),
        closed_trades=int(metrics.get("closed_trades") or 0),
        win_rate=_safe_float(metrics.get("win_rate")),
        reported_cost=_cash_reported_cost(result),
        unfilled_or_rejected=_frame_length(result.get("rejected_signals")),
        benchmark_return=_safe_float(metrics.get("benchmark_return")),
        excess_return=_safe_float(metrics.get("excess_return")),
        quality_warnings=warnings,
        result_hash=stable_payload_hash(payload),
    )


def _summarize_position_result(result: dict, mode: TradeMode) -> ModeBacktestSummary:
    if result.get("trade_mode") != mode.value:
        raise ValueError(f"{mode.value} result has a mismatched trade_mode")
    metrics = result.get("metrics") or {}
    manifest = result.get("manifest") or {}
    warnings = tuple(str(item) for item in result.get("quality_warnings") or ())
    payload = {
        "mode": mode.value,
        "status": result.get("status", "unknown"),
        "manifest": json_value(manifest),
        "metrics": json_value(metrics),
        "quality_warnings": warnings,
    }
    fees = _safe_float(metrics.get("gross_fees"))
    financing = _safe_float(metrics.get("financing_cost"))
    short_dividend_cost = _safe_float(metrics.get("short_dividend_equivalent_cost"))
    reported_cost = (
        None
        if fees is None or financing is None
        else fees + financing + (short_dividend_cost or 0.0)
    )
    return ModeBacktestSummary(
        mode=mode,
        status=str(result.get("status", "unknown")),
        run_id=manifest.get("run_id"),
        total_return=_safe_float(metrics.get("total_return")),
        maximum_drawdown=_safe_float(metrics.get("maximum_drawdown")),
        closed_trades=int(metrics.get("closed_trades") or 0),
        win_rate=_safe_float(metrics.get("win_rate")),
        reported_cost=reported_cost,
        forced_liquidations=int(metrics.get("forced_liquidations") or 0),
        unfilled_or_rejected=(
            int(metrics.get("rejected_entries") or 0)
            + int(metrics.get("deferred_exits") or 0)
        ),
        benchmark_return=_safe_float(metrics.get("benchmark_return")),
        excess_return=_safe_float(metrics.get("excess_return")),
        quality_warnings=warnings,
        result_hash=stable_payload_hash(payload),
    )


def build_mode_backtest_comparison(
    *,
    cash_result: dict,
    margin_long_result: dict,
    margin_short_result: dict,
    auto_select_decisions: tuple[AutoSelectDecision, ...],
    auto_select_result: dict | None = None,
) -> ModeBacktestComparison:
    """Normalize separate results; never select a mode from realized performance."""

    summaries = [
        _summarize_cash(cash_result),
        _summarize_position_result(margin_long_result, TradeMode.MARGIN_LONG),
        _summarize_position_result(margin_short_result, TradeMode.MARGIN_SHORT),
    ]
    if auto_select_result is None:
        auto_status = "selection_ready_execution_pending"
        auto_reasons = (
            "auto_select_series_not_supplied",
            "corporate_action_coverage_not_verified",
        )
    else:
        if auto_select_result.get("trade_mode") != TradeMode.AUTO_SELECT.value:
            raise ValueError("auto_select result has a mismatched trade_mode")
        summaries.append(
            _summarize_position_result(auto_select_result, TradeMode.AUTO_SELECT)
        )
        auto_status = str(auto_select_result.get("status", "unknown"))
        auto_reasons = tuple(
            str(item) for item in auto_select_result.get("reason_codes") or ()
        )
    payload = {
        "version": MODE_COMPARISON_VERSION,
        "summaries": [summary.model_dump(mode="json") for summary in summaries],
        "auto_select_decisions": [
            decision.model_dump(mode="json")
            for decision in sorted(
                auto_select_decisions,
                key=lambda item: (item.decision_at, item.decision_id),
            )
        ],
        "auto_select_series_status": auto_status,
        "auto_select_series_reason_codes": auto_reasons,
        "auto_select_result": (
            None
            if auto_select_result is None
            else {
                "status": auto_select_result.get("status"),
                "trade_mode": auto_select_result.get("trade_mode"),
                "manifest": json_value(auto_select_result.get("manifest") or {}),
                "metrics": json_value(auto_select_result.get("metrics") or {}),
                "reason_codes": list(auto_reasons),
            }
        ),
    }
    return ModeBacktestComparison(
        summaries=tuple(summaries),
        auto_select_decisions=tuple(
            sorted(
                auto_select_decisions,
                key=lambda item: (item.decision_at, item.decision_id),
            )
        ),
        auto_select_series_status=auto_status,
        auto_select_series_reason_codes=auto_reasons,
        input_hash=stable_payload_hash(payload),
    )
