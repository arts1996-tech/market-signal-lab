import pytest

from app.services.simulation_review_service import persist_simulation_review
from tests.test_simulation_review import (
    REVIEWED_AT,
    _decision,
    _trade,
)
from app.backtest.simulation_review import review_completed_trade


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


def _review():
    return review_completed_trade(
        _decision(),
        _trade(),
        reviewed_at=REVIEWED_AT,
    )


def test_review_persistence_is_append_only_and_idempotent():
    session = _Session()

    row, created = persist_simulation_review(session, _review())

    assert created
    assert row.subject == "executed_trade"
    assert row.decision_id == "decision-1"
    assert row.included_in_performance
    assert row.research_only
    assert row.result["decision"]["reason_codes"] == ["trend_up"]
    assert row.result["outcome"]["exit_reason"] == "take_profit"
    assert row.result["outcome"]["deducted_costs"]["fees"] == 100
    assert session.added == [row]
    assert session.flush_count == 1

    retry_session = _Session(existing=row)
    retry, retry_created = persist_simulation_review(retry_session, _review())
    assert retry is row
    assert not retry_created
    assert retry_session.added == []


def test_review_persistence_rejects_forged_id_and_changed_content():
    review = _review()
    forged = review.model_copy(update={"review_id": "0" * 64})
    with pytest.raises(ValueError, match="does not match"):
        persist_simulation_review(_Session(), forged)

    forged_input = review.model_copy(update={"review_input_hash": "1" * 64})
    with pytest.raises(ValueError, match="review_input_hash"):
        persist_simulation_review(_Session(), forged_input)

    row, _ = persist_simulation_review(_Session(), review)
    changed = review.model_copy(
        update={"quality_warnings": (*review.quality_warnings, "changed")}
    )
    with pytest.raises(ValueError, match="different immutable content"):
        persist_simulation_review(_Session(existing=row), changed)
