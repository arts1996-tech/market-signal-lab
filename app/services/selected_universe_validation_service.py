"""Prevent selected-universe validation windows from being relabeled after selection."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import SelectedUniverseValidationClaim, UserAssetSelection


PRECOMMITTED_UNSEEN = "precommitted_unseen"
RETROSPECTIVE_USER_SELECTED = "retrospective_user_selected"


def classify_selected_universe_period(
    selection: UserAssetSelection, *, period_start: date, period_end: date
) -> str:
    """A period is formal only when the persisted selection predates its start."""

    if period_start > period_end:
        raise ValueError("validation period start must not be after its end")
    if selection.created_at is None:
        raise ValueError("selection created_at is required for validation classification")
    effective_date = selection.effective_from.date()
    created_date = selection.created_at.date()
    if effective_date <= period_start and created_date <= period_start:
        return PRECOMMITTED_UNSEEN
    return RETROSPECTIVE_USER_SELECTED


def claim_selected_universe_validation_period(
    session: Session,
    *,
    selection_id: str,
    period_start: date,
    period_end: date,
    strategy_version: str,
    input_data_version: str,
) -> tuple[SelectedUniverseValidationClaim, bool]:
    """Claim an immutable period and reject formal reuse after a collection change."""

    selection = session.get(UserAssetSelection, selection_id)
    if selection is None:
        raise ValueError("user asset selection does not exist")
    classification = classify_selected_universe_period(
        selection, period_start=period_start, period_end=period_end
    )
    existing = session.scalar(
        select(SelectedUniverseValidationClaim).where(
            SelectedUniverseValidationClaim.selection_id == selection.id,
            SelectedUniverseValidationClaim.period_start == period_start,
            SelectedUniverseValidationClaim.period_end == period_end,
            SelectedUniverseValidationClaim.strategy_version == strategy_version,
            SelectedUniverseValidationClaim.input_data_version == input_data_version,
        )
    )
    if existing is not None:
        return existing, False
    if classification == PRECOMMITTED_UNSEEN:
        claimed_by_other_version = session.scalar(
            select(SelectedUniverseValidationClaim).where(
                SelectedUniverseValidationClaim.selection_key == selection.selection_key,
                SelectedUniverseValidationClaim.period_start == period_start,
                SelectedUniverseValidationClaim.period_end == period_end,
                SelectedUniverseValidationClaim.classification == PRECOMMITTED_UNSEEN,
            )
        )
        if claimed_by_other_version is not None:
            raise ValueError("formal validation period is already claimed by this selection key")
    claim = SelectedUniverseValidationClaim(
        selection_id=selection.id,
        selection_key=selection.selection_key,
        selection_version=selection.version,
        period_start=period_start,
        period_end=period_end,
        strategy_version=strategy_version,
        input_data_version=input_data_version,
        classification=classification,
    )
    session.add(claim)
    session.flush()
    return claim, True
