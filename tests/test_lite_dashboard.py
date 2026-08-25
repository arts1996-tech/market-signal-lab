from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

from app.services import lite_dashboard_service
from app.services.lite_dashboard_service import _account_summary


def test_lite_dashboard_is_a_separate_lazy_read_only_entrypoint():
    source = Path("app/lite_dashboard/streamlit_app.py").read_text(encoding="utf-8")

    assert 'page_title="Market Signal Lite"' in source
    assert '["ホーム", "仮想口座", "利用範囲"]' in source
    assert 'if active_page == "ホーム":' in source
    assert 'if active_page == "仮想口座":' in source
    assert 'if active_page == "利用範囲":' in source
    assert "load_lite_market_overview" in source
    assert "load_lite_virtual_accounts" in source
    assert "app.analysis" not in source
    assert "app.backtest" not in source
    assert "SessionLocal" not in source


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
