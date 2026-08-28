from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

from app.services import lite_dashboard_service
from app.services.lite_dashboard_service import (
    _account_summary,
    _decision_summary,
    _spillover_summary,
    parse_lite_ticker_lines,
)


def test_lite_dashboard_is_a_separate_lazy_service_backed_entrypoint():
    source = Path("app/lite_dashboard/streamlit_app.py").read_text(encoding="utf-8")

    assert 'page_title="Market Signal Lite"' in source
    assert '["ホーム", "判断・日米波及", "指定ティッカー", "仮想口座", "利用範囲"]' in source
    assert 'if active_page == "ホーム":' in source
    assert 'if active_page == "判断・日米波及":' in source
    assert 'if active_page == "指定ティッカー":' in source
    assert 'if active_page == "仮想口座":' in source
    assert 'if active_page == "利用範囲":' in source
    assert "load_lite_market_overview" in source
    assert "load_lite_research_results" in source
    assert "load_lite_selections" in source
    assert "load_lite_virtual_accounts" in source
    assert "app.analysis" not in source
    assert "app.backtest" not in source
    assert "SessionLocal" not in source


def test_lite_ticker_parser_requires_complete_exact_identity():
    rows, errors = parse_lite_ticker_lines("jp, jpx, 13060\nus,NASDAQ,nvda")

    assert errors == []
    assert rows == [
        {"market": "jp", "exchange": "JPX", "symbol": "13060"},
        {"market": "us", "exchange": "NASDAQ", "symbol": "NVDA"},
    ]

    rows, errors = parse_lite_ticker_lines("13060\nxx,JPX,13060")

    assert rows == []
    assert [error["line"] for error in errors] == [1, 2]


def test_lite_saved_result_summaries_do_not_infer_missing_values():
    spillover = SimpleNamespace(
        base_symbol="SP500",
        target_symbol="NIKKEI225",
        target_metric="daily_return",
        window_days=60,
        sample_size=55,
        r_squared=Decimal("0.12"),
        period_start="2026-01-01",
        period_end="2026-04-01",
        computed_at="2026-04-02",
        model_version="spillover-v1",
        source_policy_version="source-v1",
        analysis_status="current",
        details={"coefficients": {}, "p_values": {}, "note": "association only"},
    )
    event = SimpleNamespace(
        event_id="decision-1",
        event_at="2026-08-25T09:00:00+09:00",
        session_date="2026-08-25",
        decision_track="delayed_historical",
        input_data_version="input-v1",
        payload={"symbol": "13060", "decision": "待機", "reasons": ["quality gate"]},
    )
    account = SimpleNamespace(account_name="short_term", label="短期")

    spillover_summary = _spillover_summary(spillover)
    decision_summary = _decision_summary(event, account)

    assert spillover_summary["coefficient"] is None
    assert spillover_summary["p_value"] is None
    assert decision_summary["decision_track"] == "delayed_historical"
    assert decision_summary["counterarguments"] == []
    assert decision_summary["human_review_required"] is True


def test_lite_dashboard_discloses_delayed_research_and_no_broker_orders():
    source = Path("app/lite_dashboard/streamlit_app.py").read_text(encoding="utf-8")

    for text in [
        "遅延データによる研究版",
        "現在の売買判断",
        "証券会社への発注",
        "現在価格と同時点ニュースが未接続",
        "delayed_historical",
    ]:
        assert text in source


def test_lite_account_summary_preserves_track_quality_and_balances():
    account = SimpleNamespace(
        account_name="short_term",
        label="短期",
        initial_cash=Decimal("2500000"),
        currency="JPY",
    )
    state = SimpleNamespace(
        decision_track="delayed_historical",
        session_date="2026-08-25",
        price_latest_session="2026-05-29",
        data_delay_days=60,
        quality_gate_status="blocked",
        quality_gate_reasons=["stale_price"],
        status="recorded",
        cash=Decimal("2400000"),
        equity=Decimal("2510000"),
        realized_pnl=Decimal("10000"),
        unrealized_pnl=Decimal("0"),
        cumulative_pnl=Decimal("10000"),
        maximum_drawdown=Decimal("-0.02"),
        positions=[{"symbol": "13060"}],
        pending_orders=[],
        risk_halted=False,
    )

    result = _account_summary(account, state)

    assert result["initial_cash"] == 2_500_000
    assert result["decision_track"] == "delayed_historical"
    assert result["quality_gate_reasons"] == ["stale_price"]
    assert result["equity"] == 2_510_000
    assert result["position_count"] == 1


def test_lite_account_summary_handles_missing_state_without_fabrication():
    account = SimpleNamespace(
        account_name="mid_term",
        label="中期",
        initial_cash=Decimal("2500000"),
        currency="JPY",
    )

    result = _account_summary(account, None)

    assert result["recorded"] is False
    assert result["reason"] == "delayed_historical_state_missing"
    assert "equity" not in result


def test_lite_market_overview_degrades_when_database_is_unavailable(monkeypatch):
    def unavailable_session():
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(lite_dashboard_service, "SessionLocal", unavailable_session)

    result = lite_dashboard_service.load_lite_market_overview()

    assert result["database_available"] is False
    assert result["current_market_available"] is False
    assert "PostgreSQL" in result["current_market_reason"]


def test_lite_virtual_accounts_degrade_when_database_is_unavailable(monkeypatch):
    def unavailable_session():
        raise SQLAlchemyError("database unavailable")

    monitor = {"warnings": ["database_unavailable"]}
    monkeypatch.setattr(lite_dashboard_service, "SessionLocal", unavailable_session)
    monkeypatch.setattr(
        lite_dashboard_service,
        "load_forward_account_monitor",
        lambda: monitor,
    )

    result = lite_dashboard_service.load_lite_virtual_accounts()

    assert result["database_available"] is False
    assert result["accounts"] == []
    assert result["monitor"] == monitor
