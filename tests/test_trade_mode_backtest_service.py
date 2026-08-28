import pandas as pd
import pytest

from app.analysis.trade_modes import TradeMode
from app.backtest.audit import stable_payload_hash
from app.services.trade_mode_backtest_service import persist_trade_mode_backtest_result


class _Session:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.flush_count = 0

    def scalar(self, _query):
        return self.existing

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_count += 1


def _result(mode=TradeMode.MARGIN_LONG, **changes):
    manifest_payload = {
        "strategy_version": "strategy-v1",
        "execution_version": "execution-v1",
        "trade_mode": mode.value,
        "real_order_sent": False,
    }
    result = {
        "status": "success",
        "scope": "trade_mode_research_backtest",
        "trade_mode": mode.value,
        "account_name": f"{mode.value}-short",
        "initial_cash": 2_500_000,
        "manifest": {
            **manifest_payload,
            "run_id": stable_payload_hash(manifest_payload),
        },
        "metrics": {"total_return": 0.01},
        "events": pd.DataFrame([{"event_type": "entry", "details": {"x": 1}}]),
        "snapshots": pd.DataFrame([{"equity": 2_525_000}]),
        "real_order_sent": False,
    }
    result.update(changes)
    return result


def test_persistence_serializes_frames_and_is_append_only():
    session = _Session()

    row, created = persist_trade_mode_backtest_result(
        session,
        result=_result(),
        trade_mode=TradeMode.MARGIN_LONG,
        horizon="short_term",
        data_scope="synthetic_research",
    )

    assert created
    assert row.trade_mode == "margin_long"
    assert row.initial_cash == 2_500_000
    assert row.research_only
    assert row.result["events"] == [{"event_type": "entry", "details": {"x": 1}}]
    assert session.added == [row]
    assert session.flush_count == 1

    retry_session = _Session(existing=row)
    retry, retry_created = persist_trade_mode_backtest_result(
        retry_session,
        result=_result(),
        trade_mode=TradeMode.MARGIN_LONG,
        horizon="short_term",
        data_scope="synthetic_research",
    )
    assert retry is row
    assert not retry_created
    assert retry_session.added == []


def test_persistence_rejects_real_order_current_market_or_mode_mismatch():
    with pytest.raises(ValueError, match="disable real orders"):
        persist_trade_mode_backtest_result(
            _Session(),
            result=_result(real_order_sent=True),
            trade_mode=TradeMode.MARGIN_LONG,
            horizon="short_term",
            data_scope="synthetic_research",
        )
    with pytest.raises(ValueError, match="research data scopes"):
        persist_trade_mode_backtest_result(
            _Session(),
            result=_result(),
            trade_mode=TradeMode.MARGIN_LONG,
            horizon="short_term",
            data_scope="current_market",
        )
    with pytest.raises(ValueError, match="does not match"):
        persist_trade_mode_backtest_result(
            _Session(),
            result=_result(),
            trade_mode=TradeMode.MARGIN_SHORT,
            horizon="short_term",
            data_scope="synthetic_research",
        )


def test_same_run_id_with_changed_content_is_rejected():
    first_session = _Session()
    row, _ = persist_trade_mode_backtest_result(
        first_session,
        result=_result(),
        trade_mode=TradeMode.MARGIN_LONG,
        horizon="short_term",
        data_scope="synthetic_research",
    )
    changed = _result(metrics={"total_return": 0.99})

    with pytest.raises(ValueError, match="different immutable content"):
        persist_trade_mode_backtest_result(
            _Session(existing=row),
            result=changed,
            trade_mode=TradeMode.MARGIN_LONG,
            horizon="short_term",
            data_scope="synthetic_research",
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"manifest": {"run_id": "z" * 64, "real_order_sent": False}},
        {"initial_cash": 0},
    ],
)
def test_persistence_rejects_invalid_run_hash_and_initial_cash(changes):
    with pytest.raises(ValueError):
        persist_trade_mode_backtest_result(
            _Session(),
            result=_result(**changes),
            trade_mode=TradeMode.MARGIN_LONG,
            horizon="short_term",
            data_scope="synthetic_research",
        )
