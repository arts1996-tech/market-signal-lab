"""Append-only persistence boundary for MT-P4 research backtest series."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.trade_modes import TradeMode
from app.backtest.audit import json_value, stable_payload_hash
from app.database.models import TradeModeBacktestRun


def _json_payload(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return [_json_payload(item) for item in value.to_dict(orient="records")]
    if isinstance(value, pd.Series):
        return [_json_payload(item) for item in value.tolist()]
    if isinstance(value, BaseModel):
        return _json_payload(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_payload(item) for item in value]
    return json_value(value)


def persist_trade_mode_backtest_result(
    session: Session,
    *,
    result: dict,
    trade_mode: TradeMode,
    horizon: str,
    data_scope: str,
) -> tuple[TradeModeBacktestRun, bool]:
    """Persist one immutable series; an identical run_id is idempotent."""

    if horizon not in {"short_term", "mid_term"}:
        raise ValueError("horizon must be short_term or mid_term")
    if data_scope not in {"synthetic_research", "delayed_historical"}:
        raise ValueError("MT-P4 persistence accepts research data scopes only")
    manifest = result.get("manifest") or {}
    run_id = str(manifest.get("run_id") or "")
    if len(run_id) != 64 or any(
        character not in "0123456789abcdef" for character in run_id
    ):
        raise ValueError("backtest manifest requires a SHA-256 run_id")
    result_mode = result.get("trade_mode")
    if result_mode is not None and result_mode != trade_mode.value:
        raise ValueError("result trade_mode does not match persistence mode")
    if result.get("real_order_sent") is not False:
        raise ValueError("trade mode backtest must explicitly disable real orders")
    if manifest.get("real_order_sent") not in {None, False}:
        raise ValueError("backtest manifest cannot represent a real order")
    status = str(result.get("status") or "success")
    if status not in {"success", "insufficient_data"}:
        raise ValueError("unsupported trade mode backtest status")
    initial_cash = float(result.get("initial_cash") or 0)
    if initial_cash <= 0:
        raise ValueError("trade mode backtest initial_cash must be positive")
    serialized = _json_payload(result)
    input_hash = stable_payload_hash(
        {
            "trade_mode": trade_mode.value,
            "horizon": horizon,
            "data_scope": data_scope,
            "result": serialized,
        }
    )
    existing = session.scalar(
        select(TradeModeBacktestRun).where(TradeModeBacktestRun.run_id == run_id)
    )
    if existing is not None:
        if existing.input_hash != input_hash:
            raise ValueError("existing run_id has different immutable content")
        return existing, False

    row = TradeModeBacktestRun(
        run_id=run_id,
        scope=str(result.get("scope") or "trade_mode_research_backtest"),
        horizon=horizon,
        trade_mode=trade_mode.value,
        account_name=str(result.get("account_name") or f"{trade_mode.value}_{horizon}"),
        initial_cash=initial_cash,
        strategy_version=str(manifest.get("strategy_version") or "unknown"),
        execution_version=str(manifest.get("execution_version") or "unknown"),
        data_scope=data_scope,
        status=status,
        research_only=True,
        input_hash=input_hash,
        result=serialized,
    )
    session.add(row)
    session.flush()
    return row, True
