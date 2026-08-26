from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.services.selected_universe_validation_service import (
    PRECOMMITTED_UNSEEN,
    RETROSPECTIVE_USER_SELECTED,
    classify_selected_universe_period,
)


def _selection(*, created_at: datetime, effective_from: datetime):
    return SimpleNamespace(created_at=created_at, effective_from=effective_from)


def test_selection_period_is_formal_only_when_selection_was_frozen_before_period():
    selection = _selection(
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        effective_from=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert classify_selected_universe_period(
        selection, period_start=date(2026, 1, 5), period_end=date(2026, 1, 31)
    ) == PRECOMMITTED_UNSEEN


def test_selection_period_after_the_fact_is_labeled_retrospective_and_invalid_range_is_rejected():
    selection = _selection(
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
        effective_from=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert classify_selected_universe_period(
        selection, period_start=date(2026, 1, 5), period_end=date(2026, 1, 31)
    ) == RETROSPECTIVE_USER_SELECTED
    with pytest.raises(ValueError, match="start"):
        classify_selected_universe_period(
            selection, period_start=date(2026, 2, 1), period_end=date(2026, 1, 31)
        )
