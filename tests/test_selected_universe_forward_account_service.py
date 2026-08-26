import pandas as pd
import pytest

from app.services.selected_universe_forward_account_service import (
    enforce_selected_cash_universe,
    selected_account_rules,
)


def _assets():
    return [
        {
            "asset_id": "jp",
            "symbol": "1306",
            "exchange": "JPX",
            "currency": "JPY",
            "item_status": "active",
        },
        {
            "asset_id": "us",
            "symbol": "NVDA",
            "exchange": "NASDAQ",
            "currency": "USD",
            "item_status": "active",
        },
        {
            "asset_id": "removed",
            "symbol": "9999",
            "exchange": "JPX",
            "currency": "JPY",
            "item_status": "inactive",
        },
    ]


def test_selected_cash_boundary_allows_only_active_jpx_jpy_long_entries():
    signals = pd.DataFrame(
        [
            {"symbol": "1306", "side": "long", "score": 80},
            {"symbol": "NVDA", "side": "long", "score": 80},
            {"symbol": "9999", "side": "long", "score": 80},
            {"symbol": "7203", "side": "long", "score": 80},
            {"symbol": "1306", "side": "short", "score": 80},
        ]
    )

    eligible, rejected = enforce_selected_cash_universe(signals, _assets())

    assert eligible[["symbol", "side"]].to_dict(orient="records") == [
        {"symbol": "1306", "side": "long"}
    ]
    reasons = {
        (row["symbol"], row["side"]): row["reason_codes"]
        for row in rejected.to_dict(orient="records")
    }
    assert "cross_market_forward_account_not_yet_supported" in reasons[("NVDA", "long")]
    assert "selection_item_inactive" in reasons[("9999", "long")]
    assert "outside_allowed_selection" in reasons[("7203", "long")]
    assert "cash_long_only" in reasons[("1306", "short")]


def test_superseded_selection_rejects_new_entries_without_forcing_an_exit():
    signals = pd.DataFrame(
        [{"symbol": "1306", "side": "long", "score": 80}]
    )

    eligible, rejected = enforce_selected_cash_universe(
        signals,
        _assets(),
        allow_new_entries=False,
    )

    assert eligible.empty
    assert rejected.iloc[0]["reason_codes"] == [
        "superseded_selection_version_new_entries_forbidden"
    ]


def test_selected_account_names_are_stable_and_separate_by_selection_version_row():
    first = selected_account_rules("11111111-1111-1111-1111-111111111111")
    repeated = selected_account_rules("11111111-1111-1111-1111-111111111111")
    next_version = selected_account_rules("22222222-2222-2222-2222-222222222222")

    assert first == repeated
    assert {rule.account_name for rule in first}.isdisjoint(
        rule.account_name for rule in next_version
    )
    assert all(rule.initial_cash == 2_500_000 for rule in first)


def test_selected_account_name_rejects_non_uuid_selection_identity():
    with pytest.raises(ValueError, match="UUID"):
        selected_account_rules("not-a-selection-id")
