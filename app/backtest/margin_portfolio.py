"""Account-level MT-P4 research runner built on the MT-P3 state engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis.margin_risk import MarginAnalysisCard
from app.analysis.mode_selection import AutoSelectDecision, CashExecutionCard
from app.analysis.trade_modes import TradeMode
from app.backtest.audit import stable_payload_hash
from app.backtest.margin_position import (
    MARGIN_POSITION_ENGINE_VERSION,
    MarginAccountState,
    MarginEntryPlan,
    MarginExecutionPolicy,
    MarginOhlcBar,
    MarginPositionTerms,
    advance_margin_account_session,
    apply_position_cashflow,
    evaluate_margin_maintenance,
    margin_account_summary,
    new_margin_account,
    open_margin_position,
)


MARGIN_PORTFOLIO_STRATEGY_VERSION = "margin-portfolio-research-v1"
MARGIN_PORTFOLIO_EXECUTION_VERSION = "margin-portfolio-ohlc-v1"


class PositionCashflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cashflow_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    mode: TradeMode
    entitlement_at: datetime
    payment_at: datetime
    amount_per_share: float = Field(ge=0)
    fx_rate_to_account: float = Field(gt=0)
    cashflow_kind: Literal[
        "cash_dividend",
        "cash_distribution",
        "short_dividend_equivalent",
    ]
    available_at: datetime
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_cashflow(self):
        for value in (self.entitlement_at, self.payment_at, self.available_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("position cashflow timestamps must be timezone-aware")
        if self.payment_at < self.entitlement_at:
            raise ValueError("position cashflow payment cannot precede entitlement")
        if self.available_at > self.payment_at:
            raise ValueError("position cashflow must be available by payment")
        if self.mode == TradeMode.AUTO_SELECT:
            raise ValueError("position cashflow mode must be the executed mode")
        if self.mode == TradeMode.MARGIN_SHORT:
            if self.cashflow_kind != "short_dividend_equivalent":
                raise ValueError(
                    "short positions require a dividend-equivalent cashflow"
                )
        elif self.cashflow_kind == "short_dividend_equivalent":
            raise ValueError(
                "long positions cannot receive a short dividend equivalent"
            )
        return self


class MarginBacktestCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    plan: MarginEntryPlan
    entry_bar: MarginOhlcBar
    previous_volume: float = Field(gt=0)
    terms: MarginPositionTerms
    analysis_card: MarginAnalysisCard | CashExecutionCard

    @model_validator(mode="after")
    def validate_candidate(self):
        if self.plan.mode not in {
            TradeMode.CASH,
            TradeMode.MARGIN_LONG,
            TradeMode.MARGIN_SHORT,
        }:
            raise ValueError("portfolio candidate mode is not executable")
        if self.plan.symbol != self.entry_bar.symbol:
            raise ValueError("candidate plan and entry bar symbols must match")
        if self.plan.entry_at.astimezone(timezone.utc) != self.entry_bar.price_time.astimezone(
            timezone.utc
        ):
            raise ValueError("candidate entry bar must match entry_at")
        return self


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("backtest timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _event_frame(events: tuple[dict, ...]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame()
    frame = pd.DataFrame(events)
    frame["event_at"] = pd.to_datetime(frame["event_at"], utc=True)
    return frame


def _metrics(
    snapshots: pd.DataFrame,
    events: pd.DataFrame,
    account: MarginAccountState,
    *,
    initial_cash: float,
    benchmark: pd.Series | None,
) -> dict:
    exits = events[events["event_type"] == "exit"] if not events.empty else pd.DataFrame()
    entries = events[events["event_type"] == "entry"] if not events.empty else pd.DataFrame()
    rejected = (
        events[events["event_type"] == "rejected"]
        if not events.empty
        else pd.DataFrame()
    )
    deferred = (
        events[events["event_type"].isin(["exit_deferred", "forced_liquidation_deferred"])]
        if not events.empty
        else pd.DataFrame()
    )
    cashflows = (
        events[events["event_type"] == "position_cashflow"]
        if not events.empty
        else pd.DataFrame()
    )
    realized = [
        float(details.get("realized_pnl", 0.0))
        for details in exits.get("details", pd.Series(dtype=object))
    ]
    entry_fees = sum(
        float(details.get("entry_fee", 0.0))
        for details in entries.get("details", pd.Series(dtype=object))
    )
    exit_fees = sum(
        float(details.get("exit_fee", 0.0))
        for details in exits.get("details", pd.Series(dtype=object))
    )
    signed_cashflows = [
        float(details.get("signed_amount", 0.0))
        for details in cashflows.get("details", pd.Series(dtype=object))
    ]
    open_financing = sum(
        position.accrued_financing_cost for position in account.positions
    )
    benchmark_return = None
    if benchmark is not None and not snapshots.empty:
        usable = pd.to_numeric(benchmark, errors="coerce").dropna()
        if isinstance(usable.index, pd.DatetimeIndex):
            usable.index = pd.to_datetime(usable.index, utc=True)
            start = snapshots["date"].min()
            end = snapshots["date"].max()
            usable = usable.loc[start:end]
        if len(usable) >= 2 and float(usable.iloc[0]) > 0:
            benchmark_return = float(usable.iloc[-1] / usable.iloc[0] - 1)
    ending_equity = (
        initial_cash if snapshots.empty else float(snapshots.iloc[-1]["equity"])
    )
    total_return = ending_equity / initial_cash - 1
    return {
        "total_return": total_return,
        "maximum_drawdown": (
            0.0 if snapshots.empty else float(snapshots["drawdown"].min())
        ),
        "entries": int(len(entries)),
        "closed_trades": int(len(exits)),
        "rejected_entries": int(len(rejected)),
        "deferred_exits": int(len(deferred)),
        "forced_liquidations": int(
            sum(
                str(details.get("reason", "")).startswith("forced_liquidation:")
                for details in exits.get("details", pd.Series(dtype=object))
            )
        ),
        "win_rate": (
            None if not realized else sum(value > 0 for value in realized) / len(realized)
        ),
        "gross_fees": entry_fees + exit_fees,
        "financing_cost": account.financing_cost_paid + open_financing,
        "position_cashflow_pnl": sum(signed_cashflows),
        "dividend_or_distribution_income": sum(
            max(value, 0.0) for value in signed_cashflows
        ),
        "short_dividend_equivalent_cost": sum(
            abs(min(value, 0.0)) for value in signed_cashflows
        ),
        "benchmark_return": benchmark_return,
        "excess_return": (
            None if benchmark_return is None else total_return - benchmark_return
        ),
    }


def _merge_same_open_states(
    before: MarginAccountState,
    entered: MarginAccountState,
    advanced: MarginAccountState,
    *,
    session_at: datetime,
    entry_events: tuple[dict, ...],
    advance_events: tuple[dict, ...],
) -> MarginAccountState:
    """Keep entries before exits, so same-open proceeds cannot fund new entries."""

    old_ids = {position.position_id for position in before.positions}
    new_positions = tuple(
        position for position in entered.positions if position.position_id not in old_ids
    )
    closing_cash_delta = advanced.available_cash - before.available_cash
    combined_positions = (*advanced.positions, *new_positions)
    seen_ids = [position.position_id for position in combined_positions]
    if len(seen_ids) != len(set(seen_ids)):
        raise ValueError("same-open merge produced a duplicate position")
    return before.model_copy(
        update={
            "available_cash": entered.available_cash + closing_cash_delta,
            "realized_pnl": advanced.realized_pnl,
            "financing_cost_paid": advanced.financing_cost_paid,
            "position_cashflow_pnl": advanced.position_cashflow_pnl,
            "positions": combined_positions,
            "events": (*before.events, *entry_events, *advance_events),
            "state_as_of": session_at,
            "research_only": entered.research_only or advanced.research_only,
        }
    )


def simulate_margin_mode_portfolio(
    candidates: tuple[MarginBacktestCandidate, ...],
    bars: tuple[MarginOhlcBar, ...],
    *,
    trade_mode: TradeMode,
    initial_cash: float = 2_500_000,
    account_name: str = "margin_research",
    policy: MarginExecutionPolicy | None = None,
    terms_updates_by_session: dict[datetime, tuple[MarginPositionTerms, ...]] | None = None,
    benchmark: pd.Series | None = None,
    position_cashflows: tuple[PositionCashflowEvent, ...] = (),
    corporate_action_coverage_status: str = "unverified",
    corporate_action_coverage_hash: str | None = None,
    _allow_auto_select: bool = False,
) -> dict:
    """Run one isolated long- or short-margin series with a 2.5m research account."""

    allowed_result_modes = {TradeMode.MARGIN_LONG, TradeMode.MARGIN_SHORT}
    if _allow_auto_select:
        allowed_result_modes.add(TradeMode.AUTO_SELECT)
    if trade_mode not in allowed_result_modes:
        raise ValueError("trade_mode must be margin_long or margin_short")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if corporate_action_coverage_status not in {"unverified", "verified_supported_only"}:
        raise ValueError("unsupported corporate_action_coverage_status")
    if corporate_action_coverage_status == "verified_supported_only" and (
        corporate_action_coverage_hash is None
        or len(corporate_action_coverage_hash) != 64
        or any(
            character not in "0123456789abcdef"
            for character in corporate_action_coverage_hash
        )
    ):
        raise ValueError("verified corporate-action coverage requires a hash")
    selected_policy = policy or MarginExecutionPolicy()
    if trade_mode == TradeMode.AUTO_SELECT:
        executable_modes = {
            TradeMode.CASH,
            TradeMode.MARGIN_LONG,
            TradeMode.MARGIN_SHORT,
        }
        if any(candidate.plan.mode not in executable_modes for candidate in candidates):
            raise ValueError("auto_select candidate has a non-executable mode")
    elif any(candidate.plan.mode != trade_mode for candidate in candidates):
        raise ValueError("every candidate must match the isolated trade mode")
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_id must be unique")
    cashflow_ids = [cashflow.cashflow_id for cashflow in position_cashflows]
    if len(cashflow_ids) != len(set(cashflow_ids)):
        raise ValueError("cashflow_id must be unique")
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for cashflow in position_cashflows:
        candidate = candidate_by_id.get(cashflow.candidate_id)
        if candidate is None:
            raise ValueError("position cashflow candidate does not exist")
        if cashflow.symbol != candidate.plan.symbol or cashflow.mode != candidate.plan.mode:
            raise ValueError("position cashflow identity does not match candidate")
    bar_keys = [(_utc(bar.price_time), bar.symbol) for bar in bars]
    if len(bar_keys) != len(set(bar_keys)):
        raise ValueError("bars must be unique by session and symbol")

    by_session: dict[datetime, tuple[MarginOhlcBar, ...]] = {}
    for session_at in sorted({_utc(bar.price_time) for bar in bars}):
        by_session[session_at] = tuple(
            sorted(
                (bar for bar in bars if _utc(bar.price_time) == session_at),
                key=lambda item: item.symbol,
            )
        )
    entries_by_session: dict[datetime, tuple[MarginBacktestCandidate, ...]] = {}
    for session_at in sorted({_utc(item.plan.entry_at) for item in candidates}):
        entries_by_session[session_at] = tuple(
            sorted(
                (
                    item
                    for item in candidates
                    if _utc(item.plan.entry_at) == session_at
                ),
                key=lambda item: item.candidate_id,
            )
        )
    missing_entry_sessions = set(entries_by_session).difference(by_session)
    if missing_entry_sessions:
        raise ValueError("every candidate entry session must exist in bars")
    for candidate in candidates:
        matched_bar = next(
            (
                bar
                for bar in by_session[_utc(candidate.plan.entry_at)]
                if bar.symbol == candidate.plan.symbol
            ),
            None,
        )
        if matched_bar != candidate.entry_bar:
            raise ValueError("candidate entry_bar must equal the corresponding market bar")

    normalized_updates = {
        _utc(session): updates
        for session, updates in (terms_updates_by_session or {}).items()
    }
    if set(normalized_updates).difference(by_session):
        raise ValueError("terms update sessions must exist in bars")
    cashflow_sessions = {
        _utc(cashflow.entitlement_at) for cashflow in position_cashflows
    } | {_utc(cashflow.payment_at) for cashflow in position_cashflows}
    if cashflow_sessions.difference(by_session):
        raise ValueError("cashflow entitlement and payment sessions must exist in bars")

    account = new_margin_account(
        account_name=account_name,
        initial_cash=initial_cash,
    )
    snapshots: list[dict] = []
    high_watermark = float(initial_cash)
    position_id_by_candidate: dict[str, str] = {}
    entitlements: dict[str, dict] = {}

    for session_at, session_bars in by_session.items():
        before = account
        entered = before
        entry_event_start = len(before.events)
        for candidate in entries_by_session.get(session_at, ()):
            entry = open_margin_position(
                entered,
                candidate.plan,
                candidate.entry_bar,
                previous_volume=candidate.previous_volume,
                terms=candidate.terms,
                analysis_card=candidate.analysis_card,
                policy=selected_policy,
            )
            entered = entry.account
            if entry.accepted and entry.position_id is not None:
                position_id_by_candidate[candidate.candidate_id] = entry.position_id
        entry_events = entered.events[entry_event_start:]

        if before.positions:
            old_symbols = {position.symbol for position in before.positions}
            updates = tuple(
                terms
                for terms in normalized_updates.get(session_at, ())
                if terms.symbol in old_symbols
            )
            advanced_result = advance_margin_account_session(
                before,
                session_bars,
                policy=selected_policy,
                terms_updates=updates,
            )
            advanced = advanced_result.account
            advance_events = advanced_result.session_events
        else:
            advanced = before.model_copy(update={"state_as_of": session_at})
            advance_events = ()

        account = _merge_same_open_states(
            before,
            entered,
            advanced,
            session_at=session_at,
            entry_events=entry_events,
            advance_events=advance_events,
        )
        before_position_by_id = {
            position.position_id: position for position in before.positions
        }
        for cashflow in position_cashflows:
            if _utc(cashflow.entitlement_at) != session_at:
                continue
            position_id = position_id_by_candidate.get(cashflow.candidate_id)
            position = before_position_by_id.get(position_id)
            if position is None:
                continue
            entitlements[cashflow.cashflow_id] = {
                "position_id": position.position_id,
                "quantity": position.quantity,
            }
        for cashflow in position_cashflows:
            if _utc(cashflow.payment_at) != session_at:
                continue
            entitlement = entitlements.get(cashflow.cashflow_id)
            if entitlement is None:
                continue
            account, _ = apply_position_cashflow(
                account,
                event_at=session_at,
                entitlement_at=cashflow.entitlement_at,
                candidate_id=cashflow.candidate_id,
                position_id=entitlement["position_id"],
                symbol=cashflow.symbol,
                mode=cashflow.mode,
                quantity=entitlement["quantity"],
                amount_per_share=cashflow.amount_per_share,
                fx_rate_to_account=cashflow.fx_rate_to_account,
                cashflow_kind=cashflow.cashflow_kind,
                input_hash=cashflow.input_hash,
            )
        mark_by_symbol = {bar.symbol: bar for bar in session_bars}
        account = account.model_copy(
            update={
                "positions": tuple(
                    position.model_copy(
                        update={
                            "last_mark_price": mark_by_symbol[position.symbol].close,
                            "last_fx_rate": mark_by_symbol[
                                position.symbol
                            ].fx_rate_to_account,
                        }
                    )
                    if position.opened_at == session_at
                    else position
                    for position in account.positions
                )
            }
        )
        account, maintenance_events = evaluate_margin_maintenance(
            account,
            event_at=session_at,
            policy=selected_policy,
        )
        summary = margin_account_summary(account)
        high_watermark = max(high_watermark, float(summary["equity"]))
        snapshots.append(
            {
                "date": session_at,
                **summary,
                "available_cash": account.available_cash,
                "realized_pnl": account.realized_pnl,
                "financing_cost_paid": account.financing_cost_paid,
                "position_cashflow_pnl": account.position_cashflow_pnl,
                "positions": len(account.positions),
                "drawdown": float(summary["equity"]) / high_watermark - 1,
                "maintenance_events": len(maintenance_events),
            }
        )

    snapshot_frame = pd.DataFrame(snapshots)
    event_frame = _event_frame(account.events)
    candidate_payloads = sorted(
        (candidate.model_dump(mode="json") for candidate in candidates),
        key=lambda item: item["candidate_id"],
    )
    bar_payloads = sorted(
        (bar.model_dump(mode="json") for bar in bars),
        key=lambda item: (item["price_time"], item["symbol"]),
    )
    manifest_payload = {
        "account_name": account_name,
        "initial_cash": initial_cash,
        "trade_mode": trade_mode.value,
        "strategy_version": MARGIN_PORTFOLIO_STRATEGY_VERSION,
        "execution_version": MARGIN_PORTFOLIO_EXECUTION_VERSION,
        "position_engine_version": MARGIN_POSITION_ENGINE_VERSION,
        "policy": selected_policy.model_dump(mode="json"),
        "candidates": candidate_payloads,
        "bars": bar_payloads,
        "terms_updates": {
            session.isoformat(): [terms.model_dump(mode="json") for terms in updates]
            for session, updates in sorted(normalized_updates.items())
        },
        "position_cashflows": [
            cashflow.model_dump(mode="json")
            for cashflow in sorted(position_cashflows, key=lambda item: item.cashflow_id)
        ],
        "corporate_action_coverage_status": corporate_action_coverage_status,
        "corporate_action_coverage_hash": corporate_action_coverage_hash,
    }
    manifest = {
        **manifest_payload,
        "run_id": stable_payload_hash(manifest_payload),
        "real_order_sent": False,
    }
    final_summary = margin_account_summary(account)
    return {
        "status": "success",
        "scope": (
            "auto_select_research_backtest"
            if trade_mode == TradeMode.AUTO_SELECT
            else "margin_mode_research_backtest"
        ),
        "trade_mode": trade_mode.value,
        "account_name": account_name,
        "initial_cash": float(initial_cash),
        "available_cash": account.available_cash,
        "equity": float(final_summary["equity"]),
        "realized_pnl": account.realized_pnl,
        "unrealized_pnl": (
            float(final_summary["equity"])
            - account.available_cash
            - sum(position.margin_reserved for position in account.positions)
        ),
        "positions": pd.DataFrame(
            [position.model_dump(mode="json") for position in account.positions]
        ),
        "events": event_frame,
        "transactions": (
            event_frame[event_frame["event_type"].isin(["entry", "exit"])].copy()
            if not event_frame.empty
            else pd.DataFrame()
        ),
        "rejected_entries": (
            event_frame[event_frame["event_type"] == "rejected"].copy()
            if not event_frame.empty
            else pd.DataFrame()
        ),
        "snapshots": snapshot_frame,
        "metrics": _metrics(
            snapshot_frame,
            event_frame,
            account,
            initial_cash=initial_cash,
            benchmark=benchmark,
        ),
        "manifest": manifest,
        "quality_warnings": [
            "research_only",
            *(
                ["corporate_action_coverage_unverified"]
                if corporate_action_coverage_status == "unverified"
                else []
            ),
            "not_current_market_evidence",
        ],
        "real_order_sent": False,
    }


def simulate_auto_select_portfolio(
    selected_candidates: tuple[MarginBacktestCandidate, ...],
    bars: tuple[MarginOhlcBar, ...],
    *,
    decisions: tuple[AutoSelectDecision, ...],
    initial_cash: float = 2_500_000,
    account_name: str = "auto_select_research",
    policy: MarginExecutionPolicy | None = None,
    terms_updates_by_session: dict[datetime, tuple[MarginPositionTerms, ...]] | None = None,
    benchmark: pd.Series | None = None,
    position_cashflows: tuple[PositionCashflowEvent, ...] = (),
    corporate_action_coverage_status: str = "unverified",
    corporate_action_coverage_hash: str | None = None,
) -> dict:
    """Execute only modes chosen from frozen decisions in one mixed virtual account."""

    decision_by_id = {decision.decision_id: decision for decision in decisions}
    if len(decision_by_id) != len(decisions):
        raise ValueError("auto_select decision_id must be unique")
    expected_selected = {
        decision.decision_id: decision.selected_mode
        for decision in decisions
        if decision.selected_mode is not None
    }
    candidate_by_decision = {
        candidate.decision_id: candidate for candidate in selected_candidates
    }
    if len(candidate_by_decision) != len(selected_candidates):
        raise ValueError("auto_select accepts one selected candidate per decision")
    if set(candidate_by_decision) != set(expected_selected):
        raise ValueError("selected candidates must exactly match selected decisions")
    for decision_id, candidate in candidate_by_decision.items():
        decision = decision_by_id[decision_id]
        if candidate.plan.mode != decision.selected_mode:
            raise ValueError("candidate mode does not match the frozen auto selection")
        if _utc(candidate.plan.decision_at) != _utc(decision.decision_at):
            raise ValueError("candidate decision time does not match auto selection")
        selected_evaluation = next(
            item for item in decision.evaluations if item.selected
        )
        if candidate.analysis_card.input_hash != selected_evaluation.input_hash:
            raise ValueError("candidate analysis hash does not match auto selection")

    result = simulate_margin_mode_portfolio(
        selected_candidates,
        bars,
        trade_mode=TradeMode.AUTO_SELECT,
        initial_cash=initial_cash,
        account_name=account_name,
        policy=policy,
        terms_updates_by_session=terms_updates_by_session,
        benchmark=benchmark,
        position_cashflows=position_cashflows,
        corporate_action_coverage_status=corporate_action_coverage_status,
        corporate_action_coverage_hash=corporate_action_coverage_hash,
        _allow_auto_select=True,
    )
    decision_payload = [
        decision.model_dump(mode="json")
        for decision in sorted(decisions, key=lambda item: (item.decision_at, item.decision_id))
    ]
    manifest_payload = {
        key: value for key, value in result["manifest"].items() if key != "run_id"
    }
    manifest_payload["auto_select_decisions"] = decision_payload
    result["manifest"] = {
        **manifest_payload,
        "run_id": stable_payload_hash(manifest_payload),
    }
    result["auto_select_decisions"] = decision_payload
    result["reason_codes"] = []
    return result
