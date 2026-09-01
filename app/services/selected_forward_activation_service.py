"""Explicit activation boundary for selected-universe daily forward accounts."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.backtest.audit import stable_payload_hash
from app.analysis.decision_tracks import DECISION_TRACK_DELAYED
from app.database.models import (
    SelectedUniverseForwardActivationEvent,
    UserAssetSelection,
)
from app.services.forward_account_ledger import load_latest_forward_account_states
from app.services.selected_universe_forward_account_service import selected_account_rules


ACTIVATION_VERSION = "selected-forward-activation-v1"


def _utc(value) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def latest_activation_event_by_selection(
    events: list[SelectedUniverseForwardActivationEvent],
) -> dict[str, SelectedUniverseForwardActivationEvent]:
    ordered = sorted(
        events,
        key=lambda event: (
            _utc(event.requested_at),
            _utc(event.created_at or event.requested_at),
            str(event.id or ""),
        ),
    )
    return {event.selection_id: event for event in ordered}


def _latest_events(
    session: Session, *, as_of: datetime | None = None
) -> dict[str, SelectedUniverseForwardActivationEvent]:
    query = select(SelectedUniverseForwardActivationEvent)
    if as_of is not None:
        query = query.where(
            SelectedUniverseForwardActivationEvent.requested_at <= _utc(as_of)
        )
    events = list(
        session.scalars(
            query.order_by(
                SelectedUniverseForwardActivationEvent.requested_at,
                SelectedUniverseForwardActivationEvent.created_at,
                SelectedUniverseForwardActivationEvent.id,
            )
        )
    )
    return latest_activation_event_by_selection(events)


def _has_exposure(state: dict) -> bool:
    for key in ("positions", "pending_orders"):
        value = state.get(key)
        if isinstance(value, pd.DataFrame):
            if not value.empty:
                return True
        elif value:
            return True
    return False


def explicitly_enabled_selections(
    session: Session, *, as_of: datetime | None = None
) -> list[UserAssetSelection]:
    """Return only versions whose latest explicit event is enabled and effective."""

    cutoff = _utc(as_of or datetime.now(UTC))
    enabled_ids = [
        selection_id
        for selection_id, event in _latest_events(session, as_of=cutoff).items()
        if event.enabled
    ]
    if not enabled_ids:
        return []
    return list(
        session.scalars(
            select(UserAssetSelection)
            .where(
                UserAssetSelection.id.in_(enabled_ids),
                UserAssetSelection.status == "active",
                UserAssetSelection.effective_from <= cutoff,
            )
            .order_by(UserAssetSelection.selection_key, UserAssetSelection.version)
        )
    )


def set_selected_forward_activation(
    session: Session,
    *,
    selection_id: str,
    enabled: bool,
    request_id: str,
    requested_by: str,
    requested_at: datetime | None = None,
    reason: str = "",
) -> tuple[SelectedUniverseForwardActivationEvent, bool]:
    """Append an audited user action; never infer activation from display or analysis."""

    if not request_id or len(request_id) > 64:
        raise ValueError("request_id must be between 1 and 64 characters")
    if not requested_by or len(requested_by) > 100:
        raise ValueError("requested_by must be between 1 and 100 characters")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    requested_at = _utc(requested_at or datetime.now(UTC))
    selection = session.get(UserAssetSelection, selection_id)
    if selection is None:
        raise ValueError("user asset selection does not exist")
    if selection.status != "active":
        raise ValueError("inactive selection cannot be enabled for daily forward execution")
    if _utc(selection.effective_from) > requested_at:
        raise ValueError("selection effective_from is after the activation request")

    latest_version = session.scalar(
        select(func.max(UserAssetSelection.version)).where(
            UserAssetSelection.selection_key == selection.selection_key
        )
    )
    if enabled and selection.version != latest_version:
        raise ValueError("only the latest selection version can be newly enabled")

    payload = {
        "selection_id": selection.id,
        "selection_key": selection.selection_key,
        "selection_version": selection.version,
        "composition_hash": selection.composition_hash,
        "enabled": bool(enabled),
        "request_id": request_id,
        "requested_by": requested_by,
        "requested_at": requested_at.isoformat(),
        "reason": reason,
        "activation_version": ACTIVATION_VERSION,
    }
    input_hash = stable_payload_hash(payload)
    existing_request = session.scalar(
        select(SelectedUniverseForwardActivationEvent).where(
            SelectedUniverseForwardActivationEvent.request_id == request_id
        )
    )
    if existing_request is not None:
        if existing_request.input_hash != input_hash:
            raise ValueError("activation request_id was already used with different input")
        return existing_request, False

    latest_event = _latest_events(session).get(selection.id)
    if latest_event is not None and _utc(latest_event.requested_at) > requested_at:
        raise ValueError("activation request cannot predate the latest saved event")
    if not enabled:
        if latest_event is None or not latest_event.enabled:
            raise ValueError("selected forward account is not enabled")
        account_names = {rule.account_name for rule in selected_account_rules(selection.id)}
        states = load_latest_forward_account_states(
            session,
            DECISION_TRACK_DELAYED,
            account_names=account_names,
        )
        has_exposure = any(_has_exposure(state) for state in states.values())
        if has_exposure:
            raise ValueError(
                "selected forward account cannot be disabled with open positions or orders"
            )

    event = SelectedUniverseForwardActivationEvent(
        request_id=request_id,
        selection_id=selection.id,
        enabled=bool(enabled),
        requested_at=requested_at,
        requested_by=requested_by,
        reason=reason,
        activation_version=ACTIVATION_VERSION,
        input_hash=input_hash,
    )
    session.add(event)
    session.flush()
    return event, True
