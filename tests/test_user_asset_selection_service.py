from app.services.user_asset_selection_service import _composition_hash


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
