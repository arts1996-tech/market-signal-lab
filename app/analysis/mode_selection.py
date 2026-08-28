"""Point-in-time trade-mode comparison for MT-P4 research backtests."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis.trade_modes import EligibilityStatus, TradeMode
from app.backtest.audit import stable_payload_hash


AUTO_SELECT_POLICY_VERSION = "auto-select-policy-v1"
AUTO_SELECT_DECISION_VERSION = "auto-select-decision-v1"


class ModeCandidateRisk(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    BLOCKED = "blocked"


class AutoSelectStatus(StrEnum):
    SELECTED = "selected"
    NO_ELIGIBLE_MODE = "no_eligible_mode"


class ModeSelectionCandidate(BaseModel):
    """Frozen pre-trade comparison input; no outcome or future return is accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: TradeMode
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_as_of: datetime
    eligibility_status: EligibilityStatus
    analysis_status: str = Field(min_length=1, max_length=64)
    pretrade_score: float = Field(ge=0, le=100)
    expected_risk_reward: float = Field(gt=0)
    risk_level: ModeCandidateRisk
    reason_codes: tuple[str, ...] = ()
    human_review_required: bool = False
    human_review_approved: bool = False

    @model_validator(mode="after")
    def validate_candidate(self):
        if self.mode == TradeMode.AUTO_SELECT:
            raise ValueError("auto_select cannot be a candidate mode")
        if self.data_as_of.tzinfo is None or self.data_as_of.utcoffset() is None:
            raise ValueError("candidate data_as_of must be timezone-aware")
        if self.human_review_approved and not self.human_review_required:
            raise ValueError("review approval requires human_review_required")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("candidate reason codes must not contain duplicates")
        return self


class CashExecutionCard(BaseModel):
    """Minimal deterministic cash boundary consumed by the unified research engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    mode: TradeMode = TradeMode.CASH
    as_of: datetime
    data_as_of: datetime
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_version: str = Field(default="cash-execution-card-v1", min_length=1)
    analysis_status: str = "candidate"
    human_review_required: bool = False
    human_review_approved: bool = False

    @model_validator(mode="after")
    def validate_card(self):
        if self.mode != TradeMode.CASH:
            raise ValueError("cash execution card mode must be cash")
        for value in (self.as_of, self.data_as_of):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("cash execution card timestamps must be timezone-aware")
        if self.data_as_of > self.as_of:
            raise ValueError("cash execution data cannot be after as_of")
        if self.analysis_status not in {"candidate", "warning", "blocked"}:
            raise ValueError("unsupported cash analysis status")
        if self.human_review_approved and not self.human_review_required:
            raise ValueError("review approval requires human_review_required")
        return self


class AutoSelectPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = AUTO_SELECT_POLICY_VERSION
    minimum_pretrade_score: float = Field(default=60, ge=0, le=100)
    minimum_risk_reward: float = Field(default=1.5, gt=0)
    maximum_risk_level: ModeCandidateRisk = ModeCandidateRisk.HIGH
    conservative_tie_order: tuple[TradeMode, ...] = (
        TradeMode.CASH,
        TradeMode.MARGIN_LONG,
        TradeMode.MARGIN_SHORT,
    )

    @model_validator(mode="after")
    def validate_policy(self):
        expected = {
            TradeMode.CASH,
            TradeMode.MARGIN_LONG,
            TradeMode.MARGIN_SHORT,
        }
        if set(self.conservative_tie_order) != expected:
            raise ValueError("tie order must contain cash, margin_long and margin_short once")
        if len(self.conservative_tie_order) != len(expected):
            raise ValueError("tie order must not contain duplicates")
        if self.maximum_risk_level == ModeCandidateRisk.BLOCKED:
            raise ValueError("blocked cannot be an allowed maximum risk level")
        return self


class ModeSelectionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: TradeMode
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted: bool
    selected: bool
    rejection_codes: tuple[str, ...] = ()
    pretrade_score: float
    expected_risk_reward: float
    risk_level: ModeCandidateRisk

    @model_validator(mode="after")
    def validate_evaluation(self):
        if self.mode == TradeMode.AUTO_SELECT:
            raise ValueError("auto_select cannot be an evaluated execution mode")
        if self.selected and not self.accepted:
            raise ValueError("a selected mode must be accepted")
        if self.accepted and self.rejection_codes:
            raise ValueError("an accepted mode cannot have rejection codes")
        if not self.accepted and not self.rejection_codes:
            raise ValueError("a rejected mode requires rejection codes")
        return self


class AutoSelectDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_version: str = AUTO_SELECT_DECISION_VERSION
    policy_version: str
    decision_id: str = Field(min_length=1, max_length=128)
    decision_at: datetime
    status: AutoSelectStatus
    selected_mode: TradeMode | None
    evaluations: tuple[ModeSelectionEvaluation, ...]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    real_order_sent: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        expected_modes = {
            TradeMode.CASH,
            TradeMode.MARGIN_LONG,
            TradeMode.MARGIN_SHORT,
        }
        modes = {evaluation.mode for evaluation in self.evaluations}
        if modes != expected_modes or len(self.evaluations) != len(expected_modes):
            raise ValueError("decision evaluations must contain all three modes once")
        selected = [evaluation for evaluation in self.evaluations if evaluation.selected]
        if self.status == AutoSelectStatus.SELECTED:
            if (
                self.selected_mode not in expected_modes
                or len(selected) != 1
                or selected[0].mode != self.selected_mode
            ):
                raise ValueError("selected decision state is inconsistent")
        elif self.selected_mode is not None or selected:
            raise ValueError("no-eligible decision cannot contain a selected mode")
        return self


_RISK_RANK = {
    ModeCandidateRisk.LOW: 0,
    ModeCandidateRisk.MODERATE: 1,
    ModeCandidateRisk.HIGH: 2,
    ModeCandidateRisk.BLOCKED: 3,
}


def _candidate_rejections(
    candidate: ModeSelectionCandidate,
    *,
    decision_at: datetime,
    policy: AutoSelectPolicy,
) -> tuple[str, ...]:
    reasons = list(candidate.reason_codes)
    cutoff = decision_at.astimezone(timezone.utc)
    if candidate.data_as_of.astimezone(timezone.utc) > cutoff:
        reasons.append("candidate_data_after_decision")
    if candidate.eligibility_status == EligibilityStatus.NOT_ELIGIBLE:
        reasons.append("mode_not_eligible")
    elif candidate.eligibility_status == EligibilityStatus.INSUFFICIENT_DATA:
        reasons.append("mode_data_insufficient")
    if candidate.analysis_status not in {"candidate", "warning"}:
        reasons.append(f"analysis_status_{candidate.analysis_status}")
    if candidate.risk_level == ModeCandidateRisk.BLOCKED:
        reasons.append("risk_level_blocked")
    elif _RISK_RANK[candidate.risk_level] > _RISK_RANK[policy.maximum_risk_level]:
        reasons.append("risk_level_above_policy")
    if candidate.pretrade_score < policy.minimum_pretrade_score:
        reasons.append("pretrade_score_below_minimum")
    if candidate.expected_risk_reward < policy.minimum_risk_reward:
        reasons.append("risk_reward_below_minimum")
    if candidate.human_review_required and not candidate.human_review_approved:
        reasons.append("human_review_not_approved")
    return tuple(dict.fromkeys(reasons))


def select_trade_mode(
    *,
    decision_id: str,
    decision_at: datetime,
    candidates: tuple[ModeSelectionCandidate, ...],
    policy: AutoSelectPolicy | None = None,
) -> AutoSelectDecision:
    """Select only from frozen pre-trade facts and retain every rejection reason."""

    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    selected_policy = policy or AutoSelectPolicy()
    expected_modes = {
        TradeMode.CASH,
        TradeMode.MARGIN_LONG,
        TradeMode.MARGIN_SHORT,
    }
    candidate_modes = {candidate.mode for candidate in candidates}
    if candidate_modes != expected_modes or len(candidates) != len(expected_modes):
        raise ValueError(
            "auto_select requires exactly one cash, margin_long and margin_short candidate"
        )

    rejections = {
        candidate.mode: _candidate_rejections(
            candidate,
            decision_at=decision_at,
            policy=selected_policy,
        )
        for candidate in candidates
    }
    accepted = [candidate for candidate in candidates if not rejections[candidate.mode]]
    selected_mode = None
    if accepted:
        tie_rank = {
            mode: len(selected_policy.conservative_tie_order) - index
            for index, mode in enumerate(selected_policy.conservative_tie_order)
        }
        selected_mode = max(
            accepted,
            key=lambda candidate: (
                candidate.pretrade_score,
                candidate.expected_risk_reward,
                -_RISK_RANK[candidate.risk_level],
                tie_rank[candidate.mode],
            ),
        ).mode

    evaluations = tuple(
        ModeSelectionEvaluation(
            mode=candidate.mode,
            input_hash=candidate.input_hash,
            accepted=not rejections[candidate.mode],
            selected=candidate.mode == selected_mode,
            rejection_codes=rejections[candidate.mode],
            pretrade_score=candidate.pretrade_score,
            expected_risk_reward=candidate.expected_risk_reward,
            risk_level=candidate.risk_level,
        )
        for candidate in sorted(candidates, key=lambda item: item.mode.value)
    )
    payload = {
        "decision_id": decision_id,
        "decision_at": decision_at,
        "candidates": [
            candidate.model_dump(mode="json")
            for candidate in sorted(candidates, key=lambda item: item.mode.value)
        ],
        "policy": selected_policy.model_dump(mode="json"),
    }
    return AutoSelectDecision(
        policy_version=selected_policy.version,
        decision_id=decision_id,
        decision_at=decision_at.astimezone(timezone.utc),
        status=(
            AutoSelectStatus.SELECTED
            if selected_mode is not None
            else AutoSelectStatus.NO_ELIGIBLE_MODE
        ),
        selected_mode=selected_mode,
        evaluations=evaluations,
        input_hash=stable_payload_hash(payload),
    )
