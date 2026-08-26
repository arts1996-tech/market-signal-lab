from types import SimpleNamespace

import pytest

from app.providers.margin import MarginAssetType, MarginMarket, MarginTradingSnapshot
from app.services.margin_snapshot_service import _validate_asset_identity
from tests.test_trade_modes import _snapshot


def _asset(**changes):
    values = {
        "id": "asset-1",
        "symbol": "1306",
        "exchange": "JPX",
        "currency": "JPY",
        "asset_type": "etf",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_margin_snapshot_identity_accepts_normalized_legacy_asset_types():
    snapshot = _snapshot()

    assert _validate_asset_identity(_asset(asset_type="jp_etf"), snapshot).id == "asset-1"


@pytest.mark.parametrize(
    ("asset_change", "message"),
    [
        ({"symbol": "9999"}, "symbol"),
        ({"exchange": "NYSE"}, "exchange"),
        ({"currency": "USD"}, "currency"),
        ({"asset_type": "stock"}, "asset type"),
    ],
)
def test_margin_snapshot_identity_rejects_provider_asset_mismatches(asset_change, message):
    with pytest.raises(ValueError, match=message):
        _validate_asset_identity(_asset(**asset_change), _snapshot())


def test_margin_snapshot_keeps_missing_costs_as_null():
    snapshot = MarginTradingSnapshot(
        **{
            **_snapshot().model_dump(),
            "market": MarginMarket.JP,
            "asset_type": MarginAssetType.ETF,
            "margin_interest_rate": None,
            "stock_lending_fee": None,
            "borrow_cost": None,
        }
    )

    assert snapshot.margin_interest_rate is None
    assert snapshot.stock_lending_fee is None
    assert snapshot.borrow_cost is None
