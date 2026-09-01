from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest

from app.database.models import UserAssetSelection
from app.services import selected_forward_activation_service as service
from app.services.selected_forward_activation_service import (
    _has_exposure,
    latest_activation_event_by_selection,
)


def _event(selection_id: str, *, enabled: bool, requested_at: datetime, event_id: str):
    return SimpleNamespace(
        id=event_id,
        selection_id=selection_id,
        enabled=enabled,
        requested_at=requested_at,
        created_at=requested_at,
    )


class _FakeSession:
    def __init__(self, selection, *, latest_version=None):
        self.selection = selection
        self.latest_version = latest_version or selection.version
        self.events = []

    def get(self, _model, selection_id):
        return self.selection if selection_id == self.selection.id else None

    def scalar(self, statement):
        sql = str(statement)
        if "max(user_asset_selections.version)" in sql:
            return self.latest_version
        if "selected_universe_forward_activation_events.request_id" in sql:
            params = statement.compile().params
            request_id = next(
                value for key, value in params.items() if key.startswith("request_id")
            )
            return next(
                (event for event in self.events if event.request_id == request_id),
                None,
            )
        raise AssertionError(f"unexpected scalar query: {sql}")

    def scalars(self, statement):
        sql = str(statement)
        if "selected_universe_forward_activation_events" in sql:
            return list(self.events)
        if "user_asset_selections" in sql:
            return [self.selection]
        raise AssertionError(f"unexpected scalars query: {sql}")

    def add(self, event):
        event.id = f"event-{len(self.events) + 1}"
        event.created_at = event.requested_at
        self.events.append(event)

    def flush(self):
        return None


def _selection():
    return UserAssetSelection(
        id="11111111-1111-1111-1111-111111111111",
        selection_key="watchlist",
        version=1,
        name="監視集合",
        created_by="user",
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        status="active",
        rationale="test",
        composition_hash="a" * 64,
    )


def test_latest_activation_is_derived_per_immutable_selection_version():
    now = datetime(2026, 9, 2, tzinfo=UTC)
    events = [
        _event("selection-a", enabled=True, requested_at=now, event_id="a1"),
        _event(
            "selection-b",
            enabled=True,
            requested_at=now + timedelta(minutes=1),
            event_id="b1",
        ),
        _event(
            "selection-a",
            enabled=False,
            requested_at=now + timedelta(minutes=2),
            event_id="a2",
        ),
    ]

    latest = latest_activation_event_by_selection(list(reversed(events)))

    assert latest["selection-a"].enabled is False
    assert latest["selection-b"].enabled is True


def test_open_positions_or_orders_prevent_disabling():
    assert _has_exposure({"positions": pd.DataFrame([{"symbol": "1306"}])})
    assert _has_exposure({"pending_orders": [{"symbol": "1306"}]})
    assert not _has_exposure(
        {"positions": pd.DataFrame(), "pending_orders": pd.DataFrame()}
    )


def test_explicit_activation_is_idempotent_by_request_id():
    session = _FakeSession(_selection())
    requested_at = datetime(2026, 9, 2, tzinfo=UTC)

    created, was_created = service.set_selected_forward_activation(
        session,
        selection_id=session.selection.id,
        enabled=True,
        request_id="request-1",
        requested_by="user",
        requested_at=requested_at,
    )
    repeated, repeated_created = service.set_selected_forward_activation(
        session,
        selection_id=session.selection.id,
        enabled=True,
        request_id="request-1",
        requested_by="user",
        requested_at=requested_at,
    )

    assert was_created is True
    assert repeated_created is False
    assert repeated is created
    assert len(session.events) == 1


def test_activation_rejects_request_reuse_and_old_version():
    requested_at = datetime(2026, 9, 2, tzinfo=UTC)
    session = _FakeSession(_selection())
    service.set_selected_forward_activation(
        session,
        selection_id=session.selection.id,
        enabled=True,
        request_id="request-1",
        requested_by="user",
        requested_at=requested_at,
    )
    with pytest.raises(ValueError, match="different input"):
        service.set_selected_forward_activation(
            session,
            selection_id=session.selection.id,
            enabled=False,
            request_id="request-1",
            requested_by="user",
            requested_at=requested_at,
        )

    old_session = _FakeSession(_selection(), latest_version=2)
    with pytest.raises(ValueError, match="latest selection version"):
        service.set_selected_forward_activation(
            old_session,
            selection_id=old_session.selection.id,
            enabled=True,
            request_id="request-old",
            requested_by="user",
            requested_at=requested_at,
        )


def test_disable_rejects_open_exposure(monkeypatch):
    session = _FakeSession(_selection())
    requested_at = datetime(2026, 9, 2, tzinfo=UTC)
    service.set_selected_forward_activation(
        session,
        selection_id=session.selection.id,
        enabled=True,
        request_id="request-1",
        requested_by="user",
        requested_at=requested_at,
    )
    monkeypatch.setattr(
        service,
        "load_latest_forward_account_states",
        lambda *_a, **_k: {
            "selected": {"positions": pd.DataFrame([{"symbol": "1306"}])}
        },
    )

    with pytest.raises(ValueError, match="open positions"):
        service.set_selected_forward_activation(
            session,
            selection_id=session.selection.id,
            enabled=False,
            request_id="request-2",
            requested_by="user",
            requested_at=requested_at + timedelta(minutes=1),
        )


def test_selected_daily_job_requires_explicit_activation_and_separate_lock():
    source = Path("jobs/run_selected_universe_forward.py").read_text(encoding="utf-8")

    assert "explicitly_enabled_selections" in source
    assert "SELECTED_FORWARD_LOCK" in source
    assert "HEAVY_ANALYSIS_LOCK" in source
    assert "no_explicitly_enabled_selection_versions" in source


def test_activation_migration_is_append_only():
    source = Path(
        "app/database/migrations/versions/0026_selected_forward_activations.py"
    ).read_text(encoding="utf-8")

    assert "BEFORE UPDATE OR DELETE" in source
    assert "uq_selected_forward_activation_request" in source


def test_activation_revision_fits_alembic_version_column():
    from importlib import import_module

    migration = import_module(
        "app.database.migrations.versions.0026_selected_forward_activations"
    )

    assert len(migration.revision) <= 32
