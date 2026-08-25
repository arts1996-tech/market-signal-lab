"""Read-only view models for the lightweight daily-use dashboard."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.analysis.decision_tracks import DECISION_TRACK_DELAYED
from app.core.config import get_settings
from app.database.repositories import (
    get_virtual_account_by_name,
    latest_virtual_account_daily_state,
)
from app.database.session import SessionLocal
from app.services.analysis_service import DEFAULT_SYMBOLS, load_market_status
from app.services.forward_account_monitor import (
    EXPECTED_FORWARD_ACCOUNTS,
    load_forward_account_monitor,
)


CURRENT_MARKET_UNAVAILABLE_REASON = (
    "現在価格と同じ判断時点のニュースが未接続のため、現在判断は利用できません。"
)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def load_lite_market_overview() -> dict:
    """Return only freshness and availability data; never run heavy analysis."""

    settings = get_settings()
    try:
        with SessionLocal() as session:
            market = load_market_status(session, DEFAULT_SYMBOLS)
    except SQLAlchemyError:
        return {
            "database_available": False,
            "mode": settings.market_data_mode,
            "decision_track": DECISION_TRACK_DELAYED,
            "current_market_available": False,
            "current_market_reason": "PostgreSQLへ接続できないため、分析状態を確認できません。",
            "status": {},
            "warnings": [],
        }

    return {
        "database_available": True,
        "mode": settings.market_data_mode,
        "decision_track": DECISION_TRACK_DELAYED,
        "current_market_available": False,
        "current_market_reason": CURRENT_MARKET_UNAVAILABLE_REASON,
        "status": market["status"],
        "warnings": market["warnings"],
    }


def _account_summary(account, state) -> dict:
    if account is None:
        return {
            "account_name": None,
            "label": None,
            "recorded": False,
            "reason": "virtual_account_missing",
        }
    if state is None:
        return {
            "account_name": account.account_name,
            "label": account.label,
            "initial_cash": float(account.initial_cash),
            "currency": account.currency,
            "recorded": False,
            "reason": "delayed_historical_state_missing",
        }
    return {
        "account_name": account.account_name,
        "label": account.label,
        "initial_cash": float(account.initial_cash),
        "currency": account.currency,
        "recorded": True,
        "reason": None,
        "decision_track": state.decision_track,
        "session_date": state.session_date,
        "price_latest_session": state.price_latest_session,
        "data_delay_days": state.data_delay_days,
        "quality_gate_status": state.quality_gate_status,
        "quality_gate_reasons": list(state.quality_gate_reasons or []),
        "status": state.status,
        "cash": float(state.cash),
        "equity": _optional_float(state.equity),
        "realized_pnl": float(state.realized_pnl),
        "unrealized_pnl": _optional_float(state.unrealized_pnl),
        "cumulative_pnl": _optional_float(state.cumulative_pnl),
        "maximum_drawdown": _optional_float(state.maximum_drawdown),
        "position_count": len(state.positions or []),
        "pending_order_count": len(state.pending_orders or []),
        "risk_halted": bool(state.risk_halted),
    }


def load_lite_virtual_accounts() -> dict:
    """Load short/mid delayed-research states without modifying the ledger."""

    monitor = load_forward_account_monitor()
    try:
        with SessionLocal() as session:
            accounts = []
            for account_name in EXPECTED_FORWARD_ACCOUNTS:
                account = get_virtual_account_by_name(session, account_name)
                state = (
                    None
                    if account is None
                    else latest_virtual_account_daily_state(
                        session,
                        account.id,
                        DECISION_TRACK_DELAYED,
                    )
                )
                summary = _account_summary(account, state)
                if summary["account_name"] is None:
                    summary["account_name"] = account_name
                accounts.append(summary)
    except SQLAlchemyError:
        return {
            "database_available": False,
            "decision_track": DECISION_TRACK_DELAYED,
            "accounts": [],
            "monitor": monitor,
        }

    return {
        "database_available": True,
        "decision_track": DECISION_TRACK_DELAYED,
        "accounts": accounts,
        "monitor": monitor,
    }
