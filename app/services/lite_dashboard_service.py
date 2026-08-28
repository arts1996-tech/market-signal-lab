"""Small service boundary for the lightweight daily-use dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.analysis.decision_tracks import DECISION_TRACK_DELAYED
from app.analysis.user_selection import SelectionDraft, TickerInput, resolve_selection_draft
from app.core.config import get_settings
from app.database.models import (
    Asset,
    AssetAnalysisRun,
    SpilloverModelResult,
    UserAssetSelection,
    UserAssetSelectionAnalysisResult,
    UserAssetSelectionAnalysisRun,
    UserAssetSelectionItem,
    VirtualAccount,
    VirtualAccountEvent,
)
from app.database.repositories import (
    get_virtual_account_by_name,
    latest_virtual_account_daily_state,
)
from app.database.session import SessionLocal
from app.services.analysis_service import DEFAULT_SYMBOLS, load_market_status
from app.services.asset_analysis_service import ASSET_ANALYSIS_NAME
from app.services.forward_account_monitor import (
    EXPECTED_FORWARD_ACCOUNTS,
    load_forward_account_monitor,
)
from app.services.user_asset_selection_service import (
    create_selection_version,
    deactivate_selection_version,
)
from app.services.user_selection_analysis_service import (
    snapshot_selected_universe_analysis,
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


def _spillover_summary(row: SpilloverModelResult) -> dict:
    details = dict(row.details or {})
    coefficients = details.get("coefficients") or {}
    p_values = details.get("p_values") or {}
    return {
        "base_symbol": row.base_symbol,
        "target_symbol": row.target_symbol,
        "target_metric": row.target_metric,
        "window_days": row.window_days,
        "sample_size": row.sample_size,
        "coefficient": _optional_float(coefficients.get("us_return")),
        "p_value": _optional_float(p_values.get("us_return")),
        "r_squared": _optional_float(row.r_squared),
        "period_start": row.period_start,
        "period_end": row.period_end,
        "computed_at": row.computed_at,
        "model_version": row.model_version,
        "source_policy_version": row.source_policy_version,
        "analysis_status": row.analysis_status,
        "note": details.get("note"),
    }


def _decision_summary(event: VirtualAccountEvent, account: VirtualAccount) -> dict:
    payload = dict(event.payload or {})
    return {
        "event_id": event.event_id,
        "account_name": account.account_name,
        "account_label": account.label,
        "event_at": event.event_at,
        "session_date": event.session_date,
        "decision_track": event.decision_track,
        "symbol": payload.get("symbol"),
        "name": payload.get("name", payload.get("symbol")),
        "decision": payload.get("decision", payload.get("status", "記録済み")),
        "status": payload.get("status"),
        "score": payload.get("score"),
        "direction": payload.get("direction"),
        "reason_code": payload.get("reason_code"),
        "reasons": list(payload.get("reasons") or []),
        "counterarguments": list(payload.get("counterarguments") or []),
        "quality_warnings": list(payload.get("quality_warnings") or []),
        "data_as_of": payload.get("data_as_of"),
        "input_data_version": event.input_data_version,
        "human_review_required": True,
    }


def load_lite_research_results(*, limit: int = 100) -> dict:
    """Read already-persisted spillover results and delayed decision events."""

    bounded_limit = min(max(int(limit), 1), 200)
    try:
        with SessionLocal() as session:
            spillovers = list(
                session.scalars(
                    select(SpilloverModelResult)
                    .where(SpilloverModelResult.analysis_status == "current")
                    .order_by(
                        SpilloverModelResult.period_end.desc(),
                        SpilloverModelResult.window_days,
                    )
                    .limit(bounded_limit)
                )
            )
            decision_rows = list(
                session.execute(
                    select(VirtualAccountEvent, VirtualAccount)
                    .join(VirtualAccount, VirtualAccount.id == VirtualAccountEvent.account_id)
                    .where(
                        VirtualAccountEvent.decision_track == DECISION_TRACK_DELAYED,
                        VirtualAccountEvent.event_type == "decision",
                    )
                    .order_by(VirtualAccountEvent.event_at.desc())
                    .limit(bounded_limit * 2)
                ).all()
            )
    except SQLAlchemyError:
        return {
            "database_available": False,
            "decision_track": DECISION_TRACK_DELAYED,
            "spillovers": [],
            "decisions": [],
        }

    # Standard short/mid accounts record the same decision. Preserve the account
    # labels but show one card per frozen decision payload.
    decisions_by_key: dict[tuple, dict] = {}
    for event, account in decision_rows:
        summary = _decision_summary(event, account)
        key = (
            summary["event_at"],
            summary["symbol"],
            summary["status"],
            summary["reason_code"],
            summary["input_data_version"],
        )
        existing = decisions_by_key.get(key)
        if existing is None:
            summary["account_labels"] = [summary.pop("account_label")]
            summary.pop("account_name")
            decisions_by_key[key] = summary
        elif account.label not in existing["account_labels"]:
            existing["account_labels"].append(account.label)
    decisions = list(decisions_by_key.values())[:bounded_limit]
    return {
        "database_available": True,
        "decision_track": DECISION_TRACK_DELAYED,
        "spillovers": [_spillover_summary(row) for row in spillovers],
        "decisions": decisions,
    }


def _selection_summary(selection: UserAssetSelection, items: list[tuple]) -> dict:
    return {
        "selection_id": selection.id,
        "selection_key": selection.selection_key,
        "version": selection.version,
        "name": selection.name,
        "created_by": selection.created_by,
        "effective_from": selection.effective_from,
        "status": selection.status,
        "rationale": selection.rationale,
        "composition_hash": selection.composition_hash,
        "created_at": selection.created_at,
        "items": [
            {
                "asset_id": item.asset_id,
                "symbol": asset.symbol,
                "name": asset.name,
                "exchange": asset.exchange,
                "asset_type": asset.asset_type,
                "currency": asset.currency,
                "display_order": item.display_order,
                "status": item.status,
            }
            for item, asset in items
        ],
    }


def load_lite_selections() -> dict:
    """Read current collections, immutable history and saved analysis snapshots."""

    try:
        with SessionLocal() as session:
            versions = list(
                session.scalars(
                    select(UserAssetSelection).order_by(
                        UserAssetSelection.selection_key,
                        UserAssetSelection.version.desc(),
                    )
                )
            )
            version_ids = [row.id for row in versions]
            item_rows = (
                list(
                    session.execute(
                        select(UserAssetSelectionItem, Asset)
                        .join(Asset, Asset.id == UserAssetSelectionItem.asset_id)
                        .where(UserAssetSelectionItem.selection_id.in_(version_ids))
                        .order_by(
                            UserAssetSelectionItem.selection_id,
                            UserAssetSelectionItem.display_order,
                        )
                    ).all()
                )
                if version_ids
                else []
            )
            analysis_runs = (
                list(
                    session.scalars(
                        select(UserAssetSelectionAnalysisRun)
                        .where(UserAssetSelectionAnalysisRun.selection_id.in_(version_ids))
                        .order_by(UserAssetSelectionAnalysisRun.created_at.desc())
                    )
                )
                if version_ids
                else []
            )
            analysis_run_ids = [run.id for run in analysis_runs]
            analysis_result_rows = (
                list(
                    session.execute(
                        select(UserAssetSelectionAnalysisResult, Asset)
                        .join(Asset, Asset.id == UserAssetSelectionAnalysisResult.asset_id)
                        .where(UserAssetSelectionAnalysisResult.run_id.in_(analysis_run_ids))
                        .order_by(UserAssetSelectionAnalysisResult.created_at)
                    ).all()
                )
                if analysis_run_ids
                else []
            )
            source_runs = list(
                session.scalars(
                    select(AssetAnalysisRun)
                    .where(AssetAnalysisRun.analysis_name == ASSET_ANALYSIS_NAME)
                    .order_by(AssetAnalysisRun.completed_at.desc())
                    .limit(20)
                )
            )
    except SQLAlchemyError:
        return {
            "database_available": False,
            "selections": [],
            "history": [],
            "source_runs": [],
        }

    items_by_selection: dict[str, list[tuple]] = {}
    for item, asset in item_rows:
        items_by_selection.setdefault(item.selection_id, []).append((item, asset))
    run_by_selection: dict[str, UserAssetSelectionAnalysisRun] = {}
    for run in analysis_runs:
        run_by_selection.setdefault(run.selection_id, run)
    results_by_run: dict[str, list[dict]] = {}
    for result, asset in analysis_result_rows:
        results_by_run.setdefault(result.run_id, []).append(
            {
                "symbol": asset.symbol,
                "name": asset.name,
                "analysis_status": result.analysis_status,
                "data_as_of": result.data_as_of,
                "observations": result.observations,
                "quality_reasons": list(result.quality_reasons or []),
                "positive_reasons": list((result.result or {}).get("positive_reasons") or []),
                "negative_reasons": list((result.result or {}).get("negative_reasons") or []),
                "trade_mode_eligibility": (result.result or {}).get("trade_mode_eligibility") or {},
            }
        )

    history = []
    current_by_key: dict[str, dict] = {}
    version_counts: dict[str, int] = {}
    for selection in versions:
        summary = _selection_summary(selection, items_by_selection.get(selection.id, []))
        run = run_by_selection.get(selection.id)
        summary["latest_analysis"] = None if run is None else {
            "run_id": run.id,
            "status": run.status,
            "data_scope": run.data_scope,
            "data_as_of": run.data_as_of,
            "rule_version": run.analysis_rule_version,
            "input_data_version": run.input_data_version,
            "created_at": run.created_at,
            "results": results_by_run.get(run.id, []),
        }
        history.append(summary)
        version_counts[selection.selection_key] = version_counts.get(selection.selection_key, 0) + 1
        current_by_key.setdefault(selection.selection_key, summary)
    for summary in current_by_key.values():
        summary["version_count"] = version_counts[summary["selection_key"]]

    return {
        "database_available": True,
        "selections": list(current_by_key.values()),
        "history": history,
        "source_runs": [
            {
                "run_id": run.id,
                "data_scope": run.data_scope,
                "data_as_of": run.data_as_of,
                "status": run.status,
                "rule_version": run.rule_version,
                "input_data_version": run.input_data_version,
                "completed_at": run.completed_at,
            }
            for run in source_runs
        ],
    }


def parse_lite_ticker_lines(value: str) -> tuple[list[dict], list[dict]]:
    """Parse `market,exchange,symbol` lines without guessing missing identity."""

    rows: list[dict] = []
    errors: list[dict] = []
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            errors.append({"line": line_number, "reason": "3項目をカンマ区切りで入力してください"})
            continue
        market, exchange, symbol = parts
        try:
            ticker = TickerInput(
                market=market.lower(),
                exchange=exchange.upper(),
                symbol=symbol.upper(),
            )
        except ValidationError as error:
            errors.append({"line": line_number, "reason": str(error.errors()[0]["msg"])})
            continue
        rows.append(ticker.model_dump())
    if not rows and not errors:
        errors.append({"line": None, "reason": "1銘柄以上を入力してください"})
    return rows, errors


def create_lite_selection_version(
    *,
    name: str,
    ticker_lines: str,
    rationale: str = "",
    selection_key: str | None = None,
    created_by: str = "local_user",
) -> dict:
    """Validate and append a selected-universe version after an explicit UI action."""

    ticker_rows, parse_errors = parse_lite_ticker_lines(ticker_lines)
    if parse_errors:
        return {"ok": False, "created": False, "errors": parse_errors}
    try:
        draft = SelectionDraft(
            name=name.strip(),
            created_by=created_by,
            effective_from=datetime.now(UTC),
            rationale=rationale.strip(),
            tickers=tuple(TickerInput(**row) for row in ticker_rows),
        )
    except ValidationError as error:
        return {
            "ok": False,
            "created": False,
            "errors": [{"line": None, "reason": item["msg"]} for item in error.errors()],
        }
    try:
        with SessionLocal() as session:
            assets = list(session.scalars(select(Asset)))
            asset_frame = pd.DataFrame(
                [
                    {
                        "asset_id": asset.id,
                        "symbol": asset.symbol,
                        "exchange": asset.exchange or "",
                        "asset_type": asset.asset_type,
                    }
                    for asset in assets
                ]
            )
            resolution = resolve_selection_draft(draft, asset_frame)
            if not resolution["valid"]:
                return {"ok": False, "created": False, "errors": resolution["errors"]}
            selection, created = create_selection_version(
                session,
                draft=draft,
                resolution=resolution,
                selection_key=selection_key,
                status="active",
            )
            session.commit()
            return {
                "ok": True,
                "created": created,
                "selection_id": selection.id,
                "selection_key": selection.selection_key,
                "version": selection.version,
            }
    except ValueError as error:
        return {"ok": False, "created": False, "errors": [{"line": None, "reason": str(error)}]}
    except SQLAlchemyError:
        return {
            "ok": False,
            "created": False,
            "errors": [{"line": None, "reason": "PostgreSQLへの保存に失敗しました"}],
        }


def deactivate_lite_selection(*, selection_id: str) -> dict:
    """Append an inactive collection version after an explicit UI action."""

    try:
        with SessionLocal() as session:
            selection, created = deactivate_selection_version(
                session,
                selection_id=selection_id,
                created_by="local_user",
            )
            session.commit()
            return {
                "ok": True,
                "created": created,
                "selection_id": selection.id,
                "selection_key": selection.selection_key,
                "version": selection.version,
            }
    except ValueError as error:
        return {"ok": False, "created": False, "errors": [{"reason": str(error)}]}
    except SQLAlchemyError:
        return {
            "ok": False,
            "created": False,
            "errors": [{"reason": "PostgreSQLへの保存に失敗しました"}],
        }


def create_lite_analysis_snapshot(
    *, selection_id: str, source_asset_analysis_run_id: str
) -> dict:
    """Freeze one saved analysis run after an explicit UI action."""

    try:
        with SessionLocal() as session:
            run, created = snapshot_selected_universe_analysis(
                session,
                selection_id=selection_id,
                source_asset_analysis_run_id=source_asset_analysis_run_id,
            )
            session.commit()
            return {"ok": True, "created": created, "run_id": run.id, "status": run.status}
    except ValueError as error:
        return {"ok": False, "created": False, "errors": [{"reason": str(error)}]}
    except SQLAlchemyError:
        return {
            "ok": False,
            "created": False,
            "errors": [{"reason": "PostgreSQLへの保存に失敗しました"}],
        }
