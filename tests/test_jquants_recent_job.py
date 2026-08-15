from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from jobs.collect_jquants_all_prices import (
    candidate_date_phases,
    candidate_dates,
    select_next_work,
    should_mark_target_unavailable,
    should_probe_latest_target,
    sleep_seconds_for_result,
)
from app.database.repositories import upsert_unavailable_collection_items
from jobs.collect_jquants_recent_daily_batch import collect_recent_daily_batch, recent_candidate_dates


def test_recent_candidate_dates_skip_weekends():
    result = recent_candidate_dates(date(2026, 7, 11), lag_days=91, lookback_days=3)

    assert result == ["20260410", "20260409", "20260408"]


def test_full_collection_candidates_prioritize_latest_recent_gaps_then_oldest_history():
    result = candidate_dates(date(2026, 4, 10), history_days=5)

    assert result == [
        date(2026, 4, 10),
        date(2026, 4, 9),
        date(2026, 4, 8),
        date(2026, 4, 7),
        date(2026, 4, 6),
    ]


def test_full_collection_moves_older_sessions_after_recent_window():
    result = candidate_date_phases(
        date(2026, 4, 10), history_days=10, recent_session_count=3
    )

    assert result[:3] == [
        (date(2026, 4, 10), "latest"),
        (date(2026, 4, 9), "recent_gap"),
        (date(2026, 4, 8), "recent_gap"),
    ]
    assert result[3:] == [
        (date(2026, 3, 31), "history"),
        (date(2026, 4, 1), "history"),
        (date(2026, 4, 2), "history"),
        (date(2026, 4, 3), "history"),
        (date(2026, 4, 6), "history"),
        (date(2026, 4, 7), "history"),
    ]


def test_latest_candidate_uses_previous_exchange_session_on_weekend():
    result = candidate_date_phases(
        date(2026, 4, 12), history_days=10, recent_session_count=3
    )

    assert result[0] == (date(2026, 4, 10), "latest")


def test_next_work_finishes_latest_before_selecting_recent_gap(monkeypatch):
    checked_dates = []
    commits = []
    expected_asset = SimpleNamespace(symbol="86970")
    monkeypatch.setattr(
        "jobs.collect_jquants_all_prices.collection_target_records",
        lambda *args, **kwargs: {},
    )

    def missing_assets(session, source, target_date, asset_types, limit):
        checked_dates.append(target_date)
        return [expected_asset] if target_date == date(2026, 4, 9) else []

    monkeypatch.setattr(
        "jobs.collect_jquants_all_prices.list_assets_missing_price_for_date",
        missing_assets,
    )
    monkeypatch.setattr(
        "jobs.collect_jquants_all_prices.upsert_collection_target",
        lambda *args, **kwargs: None,
    )
    session = SimpleNamespace(commit=lambda: commits.append(True))

    target_date, assets, queue_phase = select_next_work(
        session,
        today=date(2026, 7, 10),
        lag_days=91,
        history_days=10,
        limit=1,
        recent_session_count=3,
    )

    assert checked_dates == [date(2026, 4, 10), date(2026, 4, 9)]
    assert commits == [True]
    assert target_date == date(2026, 4, 9)
    assert assets == [expected_asset]
    assert queue_phase == "recent_gap"


def test_recent_candidate_dates_use_weekday_target():
    result = recent_candidate_dates(date(2026, 7, 10), lag_days=91, lookback_days=2)

    assert result == ["20260410", "20260409"]


def test_recent_collection_bootstraps_empty_asset_master(monkeypatch):
    monkeypatch.setattr(
        "jobs.collect_jquants_recent_daily_batch.list_assets_by_source",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "jobs.collect_jquants_recent_daily_batch.collect_jquants_listed_info",
        lambda *args, **kwargs: {"status": "success", "saved_assets": 200},
    )
    monkeypatch.setattr(
        "jobs.collect_jquants_recent_daily_batch.collect_jquants_daily_batch",
        lambda *args, **kwargs: {"status": "success", "success": 1, "requested": 1},
    )

    result = collect_recent_daily_batch(
        session=object(),
        today=date(2026, 7, 10),
        lag_days=91,
        lookback_days=2,
        limit=3,
        codes=None,
        asset_types=["stock", "etf"],
        bootstrap_master_limit=200,
    )

    assert result["status"] == "success"
    assert result["selected_date"] == "20260410"
    assert result["bootstrap"]["saved_assets"] == 200


def test_recent_collection_stops_when_master_bootstrap_fails(monkeypatch):
    monkeypatch.setattr(
        "jobs.collect_jquants_recent_daily_batch.list_assets_by_source",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "jobs.collect_jquants_recent_daily_batch.collect_jquants_listed_info",
        lambda *args, **kwargs: {"status": "skipped", "message": "missing API key"},
    )

    result = collect_recent_daily_batch(
        session=object(),
        today=date(2026, 7, 10),
        lag_days=91,
        lookback_days=2,
        limit=3,
        codes=None,
        asset_types=["stock", "etf"],
        bootstrap_master_limit=200,
    )

    assert result["status"] == "skipped"
    assert not result["attempts"]


def test_continuous_collector_respects_rate_limit_and_backs_off():
    assert sleep_seconds_for_result({"status": "partial", "phase": "price_collection"}) == 15
    assert sleep_seconds_for_result({"status": "skipped", "phase": "asset_master_refresh"}) == 900
    assert sleep_seconds_for_result({"status": "success", "phase": "complete"}) == 3600


def test_single_symbol_result_never_marks_an_entire_date_unavailable():
    assert not should_mark_target_unavailable(0, 1, 1, has_existing_prices=True)
    assert not should_mark_target_unavailable(0, 1, 1, has_existing_prices=False)
    assert should_mark_target_unavailable(0, 3, 3, has_existing_prices=False)


def test_latest_unavailable_target_is_probed_only_after_cooldown():
    now = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)
    target = SimpleNamespace(status="unavailable", checked_at=now - timedelta(hours=1))
    assert not should_probe_latest_target(target, now, probe_interval_hours=6)
    assert should_probe_latest_target(
        SimpleNamespace(status="unavailable", checked_at=now - timedelta(hours=6)),
        now,
        probe_interval_hours=6,
    )
    assert should_probe_latest_target(None, now, probe_interval_hours=6)


def test_no_data_upsert_replaces_legacy_terminal_status():
    class CaptureSession:
        statement = None

        def execute(self, statement):
            self.statement = statement

    session = CaptureSession()
    upsert_unavailable_collection_items(
        session,
        "jquants",
        date(2026, 4, 10),
        {"13190": SimpleNamespace(id="00000000-0000-0000-0000-000000000001")},
        {"13190": {"status": "no_data", "message": "provider returned null prices"}},
        attempted_at=None,
    )

    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "status = excluded.status" in sql
