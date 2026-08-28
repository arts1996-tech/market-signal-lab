"""Append-only persistence boundary for deterministic simulation reviews."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.audit import stable_payload_hash
from app.backtest.simulation_review import (
    SimulationReviewResult,
    simulation_review_input_hash,
)
from app.database.models import SimulationReview


def persist_simulation_review(
    session: Session,
    review: SimulationReviewResult,
) -> tuple[SimulationReview, bool]:
    """Persist one immutable review; an identical review_id is idempotent."""

    expected_input_hash = simulation_review_input_hash(
        review.decision,
        review.outcome,
        review.benchmark,
        review.factor_observations,
        review.reviewed_at,
    )
    if review.review_input_hash != expected_input_hash:
        raise ValueError("simulation review_input_hash does not match frozen inputs")
    expected_review_id = stable_payload_hash(
        {"kind": "simulation_review", "input_hash": review.review_input_hash}
    )
    if review.review_id != expected_review_id:
        raise ValueError("simulation review_id does not match review_input_hash")
    serialized = review.model_dump(mode="json")
    result_hash = stable_payload_hash(serialized)
    existing = session.scalar(
        select(SimulationReview).where(SimulationReview.review_id == review.review_id)
    )
    if existing is not None:
        if (
            existing.review_input_hash != review.review_input_hash
            or existing.result_hash != result_hash
        ):
            raise ValueError("existing review_id has different immutable content")
        return existing, False

    decision = review.decision
    row = SimulationReview(
        review_id=review.review_id,
        decision_id=decision.decision_id,
        source_reference_type=decision.source_reference_type,
        source_reference_id=decision.source_reference_id,
        asset_id=decision.asset_id,
        symbol=decision.symbol,
        horizon=decision.horizon,
        subject=review.subject.value,
        status=review.status.value,
        decision_mode=decision.decision_mode.value,
        execution_mode=(
            None if decision.execution_mode is None else decision.execution_mode.value
        ),
        data_scope=decision.data_scope,
        decision_at=decision.decision_at,
        outcome_at=review.outcome_at,
        reviewed_at=review.reviewed_at,
        review_version=review.review_version,
        decision_input_hash=decision.input_hash,
        outcome_input_hash=review.outcome_input_hash,
        review_input_hash=review.review_input_hash,
        result_hash=result_hash,
        included_in_performance=review.included_in_performance,
        research_only=True,
        result=serialized,
    )
    session.add(row)
    session.flush()
    return row, True
