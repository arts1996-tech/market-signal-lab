from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.services.analysis_service import load_fundamental_snapshots


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def scalar(self, _query):
        return SimpleNamespace(id="asset-1")

    def execute(self, _query):
        return _ScalarRows(self._rows)


def test_load_fundamental_snapshots_keeps_provenance_and_safe_details():
    disclosed_at = datetime(2026, 5, 1, tzinfo=UTC)
    fetched_at = datetime(2026, 5, 2, tzinfo=UTC)
    row = SimpleNamespace(
        disclosed_at=disclosed_at,
        period_end=date(2026, 3, 31),
        source="sec_companyfacts",
        fetched_at=fetched_at,
        sales=1_000,
        operating_profit=150,
        net_income=100,
        eps=10,
        equity=500,
        total_assets=2_000,
        operating_cashflow=120,
        details={
            "book_value_per_share": "50",
            "currency": "USD",
            "unit": "USD",
        },
    )

    result = load_fundamental_snapshots(_Session([row]), "AAPL")

    assert len(result) == 1
    assert result.iloc[0]["source"] == "sec_companyfacts"
    assert result.iloc[0]["fetched_at"] == fetched_at
    assert result.iloc[0]["disclosed_at"] == disclosed_at
    assert result.iloc[0]["period_end"] == date(2026, 3, 31)
    assert result.iloc[0]["book_value_per_share"] == 50.0
    assert result.iloc[0]["currency"] == "USD"
    assert result.iloc[0]["unit"] == "USD"


def test_load_fundamental_snapshots_does_not_infer_missing_details():
    row = SimpleNamespace(
        disclosed_at=datetime(2026, 5, 1, tzinfo=UTC),
        period_end=date(2026, 3, 31),
        source="jquants",
        fetched_at=datetime(2026, 5, 2, tzinfo=UTC),
        sales=None,
        operating_profit=None,
        net_income=None,
        eps=None,
        equity=500,
        total_assets=None,
        operating_cashflow=None,
        details={"equity": 500},
    )

    result = load_fundamental_snapshots(_Session([row]), "86970")

    assert result.iloc[0]["book_value_per_share"] is None
    assert result.iloc[0]["currency"] is None
    assert result.iloc[0]["unit"] is None
