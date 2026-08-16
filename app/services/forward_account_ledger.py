from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
import json
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.backtest.audit import json_value, stable_payload_hash
from app.backtest.forward_account import advance_forward_accounts_as_of
from app.backtest.ohlc import MarketImpactAssumptions, PortfolioRiskRules
from app.backtest.portfolio import ExecutionAssumptions
from app.database.repositories import (
    get_or_create_virtual_account,
    get_virtual_account_by_name,
    insert_virtual_account_daily_state,
    insert_virtual_account_events,
    latest_virtual_account_daily_state,
    list_virtual_accounts,
    virtual_account_daily_state_for_date,
    virtual_account_events_for_state,
)


JST = "Asia/Tokyo"
LEDGER_EXPORT_VERSION = "virtual-account-ledger-export-v1"


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _event_date(value: Any) -> date:
    return _utc_timestamp(value).tz_convert(JST).date()


def _records(value: Any) -> list[dict]:
    if isinstance(value, pd.DataFrame):
        return json_value(value.to_dict(orient="records"))
    if value is None:
        return []
    if isinstance(value, dict):
        return [json_value(value)]
    return json_value(list(value))


def build_virtual_account_daily_state(
    account: dict,
    *,
    observed_at: Any,
    decisions: pd.DataFrame | None = None,
) -> dict:
    """Build a deterministic, serializable state without its database account id."""

    observed = _utc_timestamp(observed_at)
    manifest = account.get("manifest") or {}
    run_id = str(manifest.get("run_id") or "")
    input_data_version = str(manifest.get("input_data_version") or "")
    if not run_id or not input_data_version:
        raise ValueError("forward account manifest requires run_id and input_data_version")
    required = {
        "account_name",
        "label",
        "initial_cash",
        "strategy_version",
        "state_version",
        "cash",
        "equity",
        "realized_pnl",
        "unrealized_pnl",
        "cumulative_pnl",
        "maximum_drawdown",
    }
    missing = required.difference(account)
    if missing:
        raise ValueError(f"forward account state missing fields: {sorted(missing)}")

    last_market_session = account.get("last_market_session")
    positions = _records(account.get("positions"))
    pending_orders = _records(account.get("pending_orders"))
    signal_history = _records(
        account.get("base_signal_history", account.get("signal_history"))
    )
    decision_records = _records(decisions)
    session_date = observed.tz_convert(JST).date()
    deterministic = {
        "account_name": account["account_name"],
        "session_date": session_date.isoformat(),
        "run_id": run_id,
        "input_data_version": input_data_version,
        "cash": float(account["cash"]),
        "equity": float(account["equity"]),
        "realized_pnl": float(account["realized_pnl"]),
        "unrealized_pnl": float(account["unrealized_pnl"]),
        "cumulative_pnl": float(account["cumulative_pnl"]),
        "maximum_drawdown": float(account["maximum_drawdown"]),
        "risk_halted": bool(account.get("risk_halted", False)),
        "positions": positions,
        "pending_orders": pending_orders,
        "signal_history": signal_history,
        "decisions": decision_records,
    }
    return {
        "account": {
            "account_name": str(account["account_name"]),
            "label": str(account["label"]),
            "currency": "JPY",
            "initial_cash": float(account["initial_cash"]),
            "strategy_version": str(account["strategy_version"]),
            "state_version": str(account["state_version"]),
        },
        "state": {
            "session_date": session_date,
            "observed_at": observed.to_pydatetime(),
            "last_market_session": (
                None if last_market_session is None else _utc_timestamp(last_market_session).date()
            ),
            "status": (
                "risk_halted"
                if account.get("risk_halted")
                else "positions_open"
                if positions
                else "orders_pending"
                if pending_orders
                else "recorded"
            ),
            "input_data_version": input_data_version,
            "input_hash": stable_payload_hash(deterministic),
            "run_id": run_id,
            "cash": float(account["cash"]),
            "equity": float(account["equity"]),
            "realized_pnl": float(account["realized_pnl"]),
            "unrealized_pnl": float(account["unrealized_pnl"]),
            "cumulative_pnl": float(account["cumulative_pnl"]),
            "maximum_drawdown": float(account["maximum_drawdown"]),
            "risk_halted": bool(account.get("risk_halted", False)),
            "positions": positions,
            "pending_orders": pending_orders,
            "signal_history": signal_history,
            "details": {
                "manifest": json_value(manifest),
                "metrics": json_value(account.get("metrics", {})),
                "account_rule": json_value(account.get("account_rule", {})),
                "point_in_time_decisions": decision_records,
                "warning": "仮想記録です。実注文・投資助言・利益保証ではありません。",
            },
        },
    }


def _ledger_event(
    *,
    account_name: str,
    session_date: date,
    event_type: str,
    event_at: Any,
    input_data_version: str,
    payload: dict,
) -> dict:
    serialized = json_value(payload)
    event_key = {
        "account_name": account_name,
        "session_date": session_date.isoformat(),
        "event_type": event_type,
        "event_at": _utc_timestamp(event_at).isoformat(),
        "payload": serialized,
    }
    return {
        "event_id": stable_payload_hash(event_key),
        "session_date": session_date,
        "event_type": event_type,
        "event_at": _utc_timestamp(event_at).to_pydatetime(),
        "input_data_version": input_data_version,
        "payload": serialized,
    }


def build_virtual_account_events(
    account: dict,
    *,
    observed_at: Any,
    decisions: pd.DataFrame | None = None,
) -> list[dict]:
    observed = _utc_timestamp(observed_at)
    session_date = observed.tz_convert(JST).date()
    account_name = str(account["account_name"])
    manifest = account.get("manifest") or {}
    input_data_version = str(manifest.get("input_data_version") or "")
    events: list[dict] = []

    decision_rows = _records(decisions)
    if not decision_rows:
        decision_rows = _records(
            account.get("base_signal_history", account.get("signal_history"))
        )
    for record in decision_rows:
        event_at = record.get("decision_at") or record.get("signal_date") or observed
        if _event_date(event_at) != session_date:
            continue
        events.append(
            _ledger_event(
                account_name=account_name,
                session_date=session_date,
                event_type="decision",
                event_at=event_at,
                input_data_version=input_data_version,
                payload=record,
            )
        )
        if record.get("status") not in {None, "eligible_signal"}:
            events.append(
                _ledger_event(
                    account_name=account_name,
                    session_date=session_date,
                    event_type="skip",
                    event_at=event_at,
                    input_data_version=input_data_version,
                    payload=record,
                )
            )

    for record in _records(account.get("pending_orders")):
        event_at = record.get("signal_date") or observed
        if _event_date(event_at) == session_date:
            events.append(
                _ledger_event(
                    account_name=account_name,
                    session_date=session_date,
                    event_type="planned_execution",
                    event_at=event_at,
                    input_data_version=input_data_version,
                    payload=record,
                )
            )

    for record in _records(account.get("transactions")):
        event_at = record.get("date") or observed
        if _event_date(event_at) != session_date:
            continue
        events.append(
            _ledger_event(
                account_name=account_name,
                session_date=session_date,
                event_type="execution",
                event_at=event_at,
                input_data_version=input_data_version,
                payload=record,
            )
        )
        if record.get("action") != "仮想エントリー":
            events.append(
                _ledger_event(
                    account_name=account_name,
                    session_date=session_date,
                    event_type="closure",
                    event_at=event_at,
                    input_data_version=input_data_version,
                    payload=record,
                )
            )

    for record in _records(account.get("rejected_signals")):
        event_at = record.get("entry_date") or record.get("signal_date") or observed
        if _event_date(event_at) == session_date:
            events.append(
                _ledger_event(
                    account_name=account_name,
                    session_date=session_date,
                    event_type="skip",
                    event_at=event_at,
                    input_data_version=input_data_version,
                    payload=record,
                )
            )

    balance = {
        key: account.get(key)
        for key in (
            "cash",
            "equity",
            "realized_pnl",
            "unrealized_pnl",
            "cumulative_pnl",
            "maximum_drawdown",
            "risk_halted",
        )
    }
    events.append(
        _ledger_event(
            account_name=account_name,
            session_date=session_date,
            event_type="daily_balance",
            event_at=observed,
            input_data_version=input_data_version,
            payload=balance,
        )
    )
    return events


def persist_forward_accounts(
    session: Session,
    result: dict,
    *,
    decisions: pd.DataFrame | None = None,
    observed_at: Any | None = None,
) -> dict:
    """Append both account states and their events in the caller's transaction."""

    observation_value = observed_at
    if observation_value is None:
        observation_value = result.get("state_as_of")
    if observation_value is None:
        observation_value = datetime.now(UTC)
    observed = _utc_timestamp(observation_value)
    accounts = result.get("accounts") or {}
    if set(accounts) != {"short_term", "mid_term"}:
        raise ValueError("forward ledger requires independent short_term and mid_term accounts")
    persisted: dict[str, dict] = {}
    for account_name, account_state in accounts.items():
        payload = build_virtual_account_daily_state(
            account_state,
            observed_at=observed,
            decisions=decisions,
        )
        account = get_or_create_virtual_account(session, payload["account"])
        state_values = {"account_id": account.id, **payload["state"]}
        daily_state, created = insert_virtual_account_daily_state(session, state_values)
        event_rows = [
            {
                "account_id": account.id,
                "daily_state_id": daily_state.id,
                **event,
            }
            for event in build_virtual_account_events(
                account_state,
                # A retry reuses the first frozen observation timestamp so the
                # daily-balance event remains idempotent as well.
                observed_at=daily_state.observed_at,
                decisions=decisions,
            )
        ]
        inserted_events = insert_virtual_account_events(session, event_rows)
        persisted[account_name] = {
            "account_id": account.id,
            "daily_state_id": daily_state.id,
            "session_date": daily_state.session_date,
            "created": created,
            "events_inserted": inserted_events,
            "input_data_version": daily_state.input_data_version,
            "input_hash": daily_state.input_hash,
        }
    return {"observed_at": observed, "accounts": persisted}


def advance_and_persist_forward_accounts(
    session: Session,
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    as_of: Any,
    decisions: pd.DataFrame | None = None,
    assumptions: ExecutionAssumptions | None = None,
    market_impact: MarketImpactAssumptions | None = None,
    risk_rules: PortfolioRiskRules | None = None,
) -> dict:
    """Restore, advance and append both accounts in one caller-owned transaction."""

    previous_states = load_latest_forward_account_states(session)
    result = advance_forward_accounts_as_of(
        signals,
        prices,
        as_of=as_of,
        previous_states=previous_states or None,
        assumptions=assumptions,
        market_impact=market_impact,
        risk_rules=risk_rules,
    )
    result["ledger"] = persist_forward_accounts(
        session,
        result,
        decisions=decisions,
        observed_at=as_of,
    )
    return result


def load_latest_forward_account_states(session: Session) -> dict[str, dict]:
    """Restore the latest frozen states in the format accepted by the engine."""

    restored: dict[str, dict] = {}
    for account in list_virtual_accounts(session):
        state = latest_virtual_account_daily_state(session, account.id)
        if state is None:
            continue
        restored[account.account_name] = {
            "account_name": account.account_name,
            "label": account.label,
            "initial_cash": float(account.initial_cash),
            "strategy_version": account.strategy_version,
            "state_version": account.state_version,
            "state_as_of": state.observed_at,
            "last_market_session": state.last_market_session,
            "cash": float(state.cash),
            "equity": float(state.equity),
            "realized_pnl": float(state.realized_pnl),
            "unrealized_pnl": float(state.unrealized_pnl),
            "cumulative_pnl": float(state.cumulative_pnl),
            "maximum_drawdown": float(state.maximum_drawdown),
            "risk_halted": state.risk_halted,
            "positions": pd.DataFrame(state.positions),
            "pending_orders": pd.DataFrame(state.pending_orders),
            "base_signal_history": pd.DataFrame(state.signal_history),
            "signal_history": pd.DataFrame(state.signal_history),
        }
    return restored


def export_virtual_account_day(
    session: Session,
    output_dir: str | Path,
    *,
    account_name: str,
    session_date: date,
) -> Path:
    """Export a DB-backed immutable audit copy; PostgreSQL remains authoritative."""

    account = get_virtual_account_by_name(session, account_name)
    if account is None:
        raise LookupError(f"virtual account not found: {account_name}")
    state = virtual_account_daily_state_for_date(session, account.id, session_date)
    if state is None:
        raise LookupError(f"virtual account state not found: {account_name} {session_date}")
    events = virtual_account_events_for_state(session, state.id)
    payload = {
        "export_version": LEDGER_EXPORT_VERSION,
        "record_type": "virtual_account_daily_ledger",
        "warning": "仮想記録です。実注文・投資助言・利益保証ではありません。",
        "account": {
            "account_name": account.account_name,
            "label": account.label,
            "currency": account.currency,
            "initial_cash": account.initial_cash,
            "strategy_version": account.strategy_version,
            "state_version": account.state_version,
        },
        "daily_state": {
            column: getattr(state, column)
            for column in (
                "id",
                "session_date",
                "observed_at",
                "last_market_session",
                "status",
                "input_data_version",
                "input_hash",
                "run_id",
                "cash",
                "equity",
                "realized_pnl",
                "unrealized_pnl",
                "cumulative_pnl",
                "maximum_drawdown",
                "risk_halted",
                "positions",
                "pending_orders",
                "signal_history",
                "details",
                "created_at",
            )
        },
        "events": [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "event_at": event.event_at,
                "input_data_version": event.input_data_version,
                "payload": event.payload,
                "created_at": event.created_at,
            }
            for event in events
        ],
    }
    serialized = json.dumps(
        json_value(payload), ensure_ascii=False, sort_keys=True, indent=2
    )
    directory = Path(output_dir) / account_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_date.isoformat()}.json"
    if path.exists():
        if path.read_text(encoding="utf-8") == serialized:
            return path
        raise FileExistsError(f"immutable ledger export already differs: {path}")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)
    return path
