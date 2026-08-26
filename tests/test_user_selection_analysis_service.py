from app.services.user_selection_analysis_service import build_selection_analysis_rows


def test_selected_analysis_snapshot_keeps_only_selected_assets_and_records_missing_quality():
    selection_items = [
        {"asset_id": "selected", "symbol": "1306", "exchange": "JPX"},
        {"asset_id": "missing", "symbol": "NVDA", "exchange": "NASDAQ"},
    ]
    source_results = [
        {
            "id": "source-result",
            "asset_id": "selected",
            "data_as_of": "2026-08-25T00:00:00+00:00",
            "observations": 80,
            "result": {"attention_score": 55},
        },
        {
            "id": "outside-result",
            "asset_id": "outside",
            "data_as_of": "2026-08-25T00:00:00+00:00",
            "observations": 80,
            "result": {"attention_score": 99},
        },
    ]

    rows = build_selection_analysis_rows(selection_items, source_results)

    assert [row["asset_id"] for row in rows] == ["selected", "missing"]
    assert rows[0]["analysis_status"] == "analyzed"
    assert rows[0]["result"]["source_result"] == {"attention_score": 55}
    assert rows[0]["result"]["trade_mode_eligibility"]["cash"] == "not_assessed"
    assert rows[1]["analysis_status"] == "insufficient_data"
    assert rows[1]["quality_reasons"] == ["not_eligible_in_source_analysis_run"]
    assert rows[1]["result"]["trade_mode_eligibility"]["cash"] == "unavailable_due_to_insufficient_data"
    assert all(len(row["input_hash"]) == 64 for row in rows)
