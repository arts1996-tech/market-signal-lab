from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import ValidationError

from app.analysis.user_selection import SelectionDraft, TickerInput, resolve_selection_draft


def _assets():
    return pd.DataFrame([
        {"asset_id": "jp-etf", "symbol": "1306", "exchange": "JPX", "asset_type": "etf"},
        {"asset_id": "us-stock", "symbol": "NVDA", "exchange": "NASDAQ", "asset_type": "stock"},
    ])


def test_selection_resolves_only_explicit_complete_asset_identity_and_hashes_it():
    draft = SelectionDraft(name="watch", created_by="user", effective_from=datetime(2026, 8, 26, tzinfo=UTC), tickers=(TickerInput(market="jp", exchange="JPX", symbol="1306"), TickerInput(market="us", exchange="NASDAQ", symbol="NVDA")))
    result = resolve_selection_draft(draft, _assets())
    assert result["valid"] is True
    assert [item["asset_id"] for item in result["items"]] == ["jp-etf", "us-stock"]
    assert len(result["composition_hash"]) == 64


def test_selection_rejects_unknown_duplicate_and_unsafe_ticker_inputs():
    draft = SelectionDraft(name="watch", created_by="user", effective_from=datetime(2026, 8, 26, tzinfo=UTC), tickers=(TickerInput(market="jp", exchange="JPX", symbol="1306"), TickerInput(market="jp", exchange="JPX", symbol="1306"), TickerInput(market="jp", exchange="JPX", symbol="9999")))
    result = resolve_selection_draft(draft, _assets())
    assert result["valid"] is False
    assert {error["reason"] for error in result["errors"]} == {"duplicate_asset", "asset_not_found_or_ambiguous"}
    with pytest.raises(ValidationError):
        TickerInput(market="jp", exchange="JPX", symbol="1306; DROP")
