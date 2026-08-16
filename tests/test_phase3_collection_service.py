import json
from types import SimpleNamespace

import pandas as pd

from app.core.exceptions import DataProviderError
from app.database.models import ApiFetchLog, JobRun
from app.database.repositories import (
    insert_etf_metric_snapshots,
    insert_fundamental_snapshots,
)
from app.services import phase3_collection_service as service


class _Session:
    def __init__(self, asset):
        self.asset = asset
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, _query):
        return self.asset

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _InsertedRows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _RepositorySession:
    def __init__(self, inserted):
        self.inserted = inserted
        self.statement = None

    def scalars(self, statement):
        self.statement = statement
        return _InsertedRows(self.inserted)


def _fundamental_asset(**overrides):
    values = {
        "id": "asset-1",
        "symbol": "86970",
        "source": "jquants",
        "asset_type": "stock",
        "currency": "JPY",
        "sec_cik": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_repository_snapshot_inserts_are_conflict_safe():
    fundamental_session = _RepositorySession(["new-id"])
    etf_session = _RepositorySession([])

    assert insert_fundamental_snapshots(fundamental_session, [{"asset_id": "a"}]) == 1
    assert insert_etf_metric_snapshots(etf_session, [{"asset_id": "a"}]) == 0
    assert "ON CONFLICT" in str(fundamental_session.statement)
    assert "DO NOTHING" in str(fundamental_session.statement)
    assert "ON CONFLICT" in str(etf_session.statement)
    assert "DO NOTHING" in str(etf_session.statement)


def test_jquants_single_symbol_collection_records_success_and_job_run(monkeypatch):
    class Client:
        def fetch_financial_summary(self, code, from_date, to_date):
            assert (code, from_date, to_date) == ("86970", "2026-01-01", "2026-03-31")
            return pd.DataFrame(
                [
                    {
                        "Code": "86970",
                        "DisclosedDate": "2026-02-01",
                        "CurrentPeriodEndDate": "2025-12-31",
                        "Sales": "1000",
                    }
                ]
            ), 25

    monkeypatch.setattr(service, "insert_fundamental_snapshots", lambda session, rows: 1)
    session = _Session(_fundamental_asset())

    result = service.collect_jquants_financial_summary(
        session,
        "86970",
        "2026-01-01",
        "2026-03-31",
        client=Client(),
    )

    assert result["status"] == "success"
    assert result["classification"] == "new_rows_saved"
    assert result["saved_rows"] == 1
    assert any(isinstance(row, ApiFetchLog) and row.status == "success" for row in session.added)
    assert any(
        isinstance(row, JobRun)
        and row.job_name == "collect_jquants_financial_summary"
        and row.status == "success"
        for row in session.added
    )
    assert session.commits == 1


def test_jquants_replay_is_successful_without_duplicate_write(monkeypatch):
    class Client:
        def fetch_financial_summary(self, *_args):
            return pd.DataFrame(
                [
                    {
                        "Code": "86970",
                        "DisclosedDate": "2026-02-01",
                        "CurrentPeriodEndDate": "2025-12-31",
                    }
                ]
            ), 10

    monkeypatch.setattr(service, "insert_fundamental_snapshots", lambda session, rows: 0)
    result = service.collect_jquants_financial_summary(
        _Session(_fundamental_asset()), "86970", client=Client()
    )

    assert result["status"] == "success"
    assert result["classification"] == "idempotent_replay"
    assert result["existing_rows"] == 1
    assert result["writes_database"] is False


def test_retryable_provider_failure_is_classified_without_message_leak():
    class Client:
        def fetch_financial_summary(self, *_args):
            raise DataProviderError(
                "secret response body",
                category="rate_limited",
                retryable=True,
            )

    session = _Session(_fundamental_asset())
    result = service.collect_jquants_financial_summary(session, "86970", client=Client())

    assert result["status"] == "error"
    assert result["classification"] == "rate_limited"
    assert result["retryable"] is True
    assert "secret" not in json.dumps(result)
    fetch_log = next(row for row in session.added if isinstance(row, ApiFetchLog))
    assert fetch_log.status == "retry_pending"
    assert fetch_log.message == "rate_limited"
    assert session.rollbacks == 1


def test_sec_collection_requires_exact_asset_cik_before_network_call():
    class Client:
        def fetch_companyfacts(self, _cik):
            raise AssertionError("network must not be called")

    asset = _fundamental_asset(
        symbol="AAPL",
        source="sec",
        currency="USD",
        sec_cik="0000320194",
    )
    result = service.collect_sec_fundamentals(
        _Session(asset), "0000320193", "aapl", client=Client()
    )

    assert result["status"] == "error"
    assert result["classification"] == "asset_cik_mismatch"


def test_reviewed_etf_file_uses_same_audit_path_and_is_idempotent(monkeypatch, tmp_path):
    source_file = tmp_path / "etf.json"
    source_file.write_text(
        json.dumps(
            [
                {
                    "Code": "13060",
                    "Date": "2026-04-01",
                    "ExpenseRatio": "0.15",
                    "TrackingIndex": "TOPIX",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "insert_etf_metric_snapshots", lambda session, rows: 0)
    session = _Session(_fundamental_asset(symbol="13060", asset_type="etf"))

    result = service.save_reviewed_etf_metrics(session, source_file)

    assert result["status"] == "success"
    assert result["classification"] == "idempotent_replay"
    assert result["symbols"] == ["13060"]
    assert any(
        isinstance(row, JobRun) and row.job_name == "save_etf_metrics"
        for row in session.added
    )


def test_invalid_etf_json_records_a_non_retryable_failure(tmp_path):
    source_file = tmp_path / "etf.json"
    source_file.write_text("not-json", encoding="utf-8")
    session = _Session(None)

    result = service.save_reviewed_etf_metrics(session, source_file)

    assert result["status"] == "error"
    assert result["classification"] == "invalid_json"
    assert result["retryable"] is False
