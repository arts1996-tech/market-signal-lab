from pathlib import Path

import pandas as pd
import pytest

from app.analysis.market_calendar import exchange_calendar
from app.services.mid_term_service import build_mid_term_analysis


def test_mid_term_analysis_uses_only_disclosed_fundamentals_and_prices_as_of_cutoff():
    fundamentals = pd.DataFrame(
        [
            {
                "symbol": "13060",
                "disclosed_at": "2025-05-01T00:00:00Z",
                "period_end": pd.Timestamp("2025-03-31").date(),
                "source": "jquants",
                "sales": 100,
                "operating_profit": 10,
                "net_income": 8,
                "eps": 10,
                "equity": 50,
                "total_assets": 100,
                "operating_cashflow": 12,
                "details": {"currency": "JPY", "unit": "JPY"},
            },
            {
                "symbol": "13060",
                "disclosed_at": "2026-05-01T00:00:00Z",
                "period_end": pd.Timestamp("2026-03-31").date(),
                "source": "jquants",
                "sales": 120,
                "operating_profit": 15,
                "net_income": 10,
                "eps": 12,
                "equity": 55,
                "total_assets": 110,
                "operating_cashflow": 14,
                "details": {"currency": "JPY", "unit": "JPY"},
            },
            {
                "symbol": "13060",
                "disclosed_at": "2027-05-01T00:00:00Z",
                "period_end": pd.Timestamp("2027-03-31").date(),
                "source": "jquants",
                "sales": 999,
                "details": {"currency": "JPY", "unit": "JPY"},
            },
        ]
    )
    dates = exchange_calendar("XTKS").sessions_in_range("2025-01-01", "2026-12-31")[:260]
    prices = pd.DataFrame(
        {
            "symbol": "13060",
            "price_time": dates,
            "close": [100 + index for index in range(len(dates))],
            "source": "jquants",
        }
    )

    result = build_mid_term_analysis(
        fundamentals,
        prices,
        as_of="2026-06-01T00:00:00Z",
    )
    row = result["results"][0]

    assert result["status"] == "success"
    assert row["sales_growth"] == pytest.approx(0.2)
    assert row["operating_profit_growth"] == pytest.approx(0.5)
    assert row["eps_growth"] == pytest.approx(0.2)
    assert row["operating_margin"] == pytest.approx(0.125)
    assert row["roe"] == pytest.approx(10 / 55)
    assert row["equity_ratio"] == pytest.approx(0.5)
    assert row["momentum_3m"] is not None
    assert row["momentum_6m"] is not None
    assert row["momentum_12m"] is not None
    assert row["distance_from_52week_high"] == 0.0
    assert row["warnings"] == []


def test_mid_term_analysis_reports_missing_disclosures():
    result = build_mid_term_analysis(pd.DataFrame(), pd.DataFrame(), as_of="2026-06-01")

    assert result["status"] == "insufficient_data"
    assert result["reasons"] == ["no_disclosed_fundamentals_as_of_analysis_time"]


def test_mid_term_analysis_does_not_call_empty_snapshots_success():
    fundamentals = pd.DataFrame(
        [
            {
                "symbol": "86970",
                "disclosed_at": "2026-03-25T00:00:00Z",
                "period_end": pd.Timestamp("2026-03-31").date(),
                "source": "jquants",
                "details": {},
            }
        ]
    )

    result = build_mid_term_analysis(fundamentals, pd.DataFrame(), as_of="2026-06-01")

    assert result["status"] == "insufficient_data"
    assert result["reasons"] == ["no_usable_mid_term_metrics"]
    assert "currency_unknown" in result["results"][0]["warnings"]
    assert "insufficient_3m_price_history" in result["results"][0]["warnings"]


def test_mid_term_job_no_longer_records_placeholder_success():
    source = Path("jobs/run_mid_term_analysis.py").read_text(encoding="utf-8")

    assert '"placeholder"' not in source
    assert "run_mid_term_analysis" in source
    assert 'details["status"]' in source
