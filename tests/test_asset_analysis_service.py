import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.services.asset_analysis_service import (
    ASSET_ANALYSIS_RULE_VERSION,
    ASSET_ANALYSIS_PAGE_SIZE_MAX,
    build_all_asset_analysis,
    load_asset_analysis_page,
)


def test_asset_analysis_rule_version_advances_for_support_resistance_output():
    assert ASSET_ANALYSIS_RULE_VERSION == "phase3-all-assets-v4"


def test_all_asset_analysis_does_not_truncate_universe_at_10_or_200():
    asset_count = 205
    symbols = [f"{index:05d}" for index in range(asset_count)]
    sessions = exchange_calendar("XTKS").sessions_in_range(
        "2026-04-01", "2026-05-31"
    )[:30]
    assets = pd.DataFrame(
        [
            {
                "asset_id": f"asset-{symbol}",
                "symbol": symbol,
                "name": f"Asset {symbol}",
                "asset_type": "stock",
                "metadata_json": {"sector_33": "テスト"},
            }
            for symbol in symbols
        ]
    )
    prices = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "name": f"Asset {symbol}",
                "price_time": session,
                "close": 100 + day_index + symbol_index / 1000,
                "price_basis": "raw_ohlcv_with_adjusted",
            }
            for symbol_index, symbol in enumerate(symbols)
            for day_index, session in enumerate(sessions)
        ]
    )

    result = build_all_asset_analysis(assets, prices, pd.DataFrame())

    assert result["eligible_asset_count"] == asset_count
    assert result["movement_eligible_count"] == asset_count
    assert set(result["results"]["symbol"]) == set(symbols)
    assert result["results"]["attention_rank"].max() == asset_count
    assert result["results"]["movement_rank"].max() == asset_count


def test_ui_page_size_is_bounded_without_bounding_batch_size():
    class _NoQuerySession:
        def scalar(self, _query):
            return None

    result = load_asset_analysis_page(
        _NoQuerySession(), page=1, page_size=ASSET_ANALYSIS_PAGE_SIZE_MAX
    )

    assert result["total"] == 0
    assert result["page_size"] == ASSET_ANALYSIS_PAGE_SIZE_MAX

    try:
        load_asset_analysis_page(
            _NoQuerySession(),
            page=1,
            page_size=ASSET_ANALYSIS_PAGE_SIZE_MAX + 1,
        )
    except ValueError as exc:
        assert "page_size" in str(exc)
    else:
        raise AssertionError("page sizes above the UI bound must be rejected")
