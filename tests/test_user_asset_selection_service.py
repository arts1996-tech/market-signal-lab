from datetime import UTC, datetime
from types import SimpleNamespace

from app.analysis.user_selection import SelectionDraft, TickerInput
from app.services.user_asset_selection_service import (
    _composition_hash,
    _matches_immutable_version,
)


def test_selection_composition_hash_keeps_user_order_and_complete_identity():
    first = [
        {"asset_id": "jp-etf", "symbol": "1306", "exchange": "JPX", "market": "jp"},
        {"asset_id": "us-stock", "symbol": "NVDA", "exchange": "NASDAQ", "market": "us"},
    ]
    reordered = list(reversed(first))
    changed_exchange = [
        {"asset_id": "jp-etf", "symbol": "1306", "exchange": "JPX", "market": "jp"},
        {"asset_id": "us-stock", "symbol": "NVDA", "exchange": "NYSE", "market": "us"},
    ]

    assert _composition_hash(first) != _composition_hash(reordered)
    assert _composition_hash(first) != _composition_hash(changed_exchange)


def test_selection_status_transition_is_a_new_immutable_version():
    effective_from = datetime(2026, 8, 28, tzinfo=UTC)
    draft = SelectionDraft(
        name="重点監視",
        created_by="local_user",
        effective_from=effective_from,
        rationale="initial",
        tickers=(TickerInput(market="jp", exchange="JPX", symbol="13060"),),
    )
    existing = SimpleNamespace(
        name=draft.name,
        created_by=draft.created_by,
        effective_from=draft.effective_from,
        rationale=draft.rationale,
        status="active",
    )

    assert _matches_immutable_version(existing, draft, "active") is True
    assert _matches_immutable_version(existing, draft, "inactive") is False
