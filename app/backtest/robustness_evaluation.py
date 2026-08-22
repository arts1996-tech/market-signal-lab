"""Local, non-optimizing robustness checks for walk-forward backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import pandas as pd

from app.backtest.audit import frame_hash, json_value, stable_payload_hash


ROBUSTNESS_EVALUATION_VERSION = "local-robustness-diagnostic-v1"


@dataclass(frozen=True)
class RobustnessEvaluationPolicy:
    """Versioned perturbation grid and conservative assessment thresholds."""

    version: str = ROBUSTNESS_EVALUATION_VERSION
    take_profit_relative_step: float = 0.10
    stop_loss_relative_step: float = 0.10
    score_threshold_step: int = 5
    fee_rate_step: float = 0.0005
    slippage_multiplier_step: float = 0.25
    minimum_validation_windows: int = 3
    minimum_completed_trades: int = 30
    material_absolute_return_delta: float = 0.005
    material_relative_return_delta: float = 0.50

    def __post_init__(self) -> None:
        relative_steps = (
            self.take_profit_relative_step,
            self.stop_loss_relative_step,
            self.slippage_multiplier_step,
        )
        if any(not 0 < value < 1 for value in relative_steps):
            raise ValueError("relative robustness steps must be between 0 and 1")
        if not 0 < self.score_threshold_step <= 20:
            raise ValueError("score_threshold_step must be between 1 and 20")
        if not 0 < self.fee_rate_step <= 0.005:
            raise ValueError("fee_rate_step must be between 0 and 0.005")
        if self.minimum_validation_windows < 2:
            raise ValueError("minimum_validation_windows must be at least 2")
        if self.minimum_completed_trades < 2:
            raise ValueError("minimum_completed_trades must be at least 2")
        if self.material_absolute_return_delta <= 0:
            raise ValueError("material_absolute_return_delta must be positive")
        if not 0 < self.material_relative_return_delta <= 1:
            raise ValueError("material_relative_return_delta must be between 0 and 1")


@dataclass(frozen=True)
class RobustnessVariant:
    variant_id: str
    dimension: str
    direction: str
    score_threshold: int
    stop_loss: float
    take_profit: float
    fee_rate: float
    slippage_multiplier: float


def build_robustness_variants(
    *,
    score_threshold: int,
    stop_loss: float,
    take_profit: float,
    fee_rate: float,
    policy: RobustnessEvaluationPolicy | None = None,
) -> list[RobustnessVariant]:
    """Return a fixed one-factor-at-a-time grid; never rank or select it."""

    policy = policy or RobustnessEvaluationPolicy()
    baseline = {
        "score_threshold": int(score_threshold),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
        "fee_rate": float(fee_rate),
        "slippage_multiplier": 1.0,
    }
    variants = [
        RobustnessVariant("baseline", "baseline", "baseline", **baseline)
    ]

    def variant(variant_id: str, dimension: str, direction: str, **change) -> None:
        values = {**baseline, **change}
        variants.append(
            RobustnessVariant(variant_id, dimension, direction, **values)
        )

    variant(
        "take_profit_lower",
        "take_profit",
        "lower",
        take_profit=take_profit * (1 - policy.take_profit_relative_step),
    )
    variant(
        "take_profit_higher",
        "take_profit",
        "higher",
        take_profit=take_profit * (1 + policy.take_profit_relative_step),
    )
    stop_magnitude = abs(stop_loss)
    variant(
        "stop_loss_tighter",
        "stop_loss",
        "tighter",
        stop_loss=-stop_magnitude * (1 - policy.stop_loss_relative_step),
    )
    variant(
        "stop_loss_looser",
        "stop_loss",
        "looser",
        stop_loss=-stop_magnitude * (1 + policy.stop_loss_relative_step),
    )
    variant(
        "score_threshold_lower",
        "score_threshold",
        "lower",
        score_threshold=max(0, score_threshold - policy.score_threshold_step),
    )
    variant(
        "score_threshold_higher",
        "score_threshold",
        "higher",
        score_threshold=min(100, score_threshold + policy.score_threshold_step),
    )
    variant(
        "fee_rate_lower",
        "fee_rate",
        "lower",
        fee_rate=max(0.0, fee_rate - policy.fee_rate_step),
    )
    variant(
        "fee_rate_higher",
        "fee_rate",
        "higher",
        fee_rate=fee_rate + policy.fee_rate_step,
    )
    variant(
        "slippage_lower",
        "slippage",
        "lower",
        slippage_multiplier=1 - policy.slippage_multiplier_step,
    )
    variant(
        "slippage_higher",
        "slippage",
        "higher",
        slippage_multiplier=1 + policy.slippage_multiplier_step,
    )
    if len({item.variant_id for item in variants}) != len(variants):
        raise AssertionError("robustness variant ids must be unique")
    return variants


def empty_robustness_evaluation(
    policy: RobustnessEvaluationPolicy,
    variants: list[RobustnessVariant],
) -> dict:
    return {
        "version": policy.version,
        "policy": asdict(policy),
        "variant_count": len(variants),
        "perturbation_count": max(0, len(variants) - 1),
        "parameter_selection_performed": False,
        "selected_best_variant": None,
        "multiple_comparison_correction": "not_applicable_no_hypothesis_tests",
        "overall_assessment": "not_assessed_no_validation_windows",
        "window_results": [],
        "summaries": [],
        "input_hash": stable_payload_hash([]),
        "warnings": [
            "no_completed_validation_windows",
            "multiple_comparisons_can_create_false_impressions",
            "diagnostic_only_do_not_select_best_variant",
        ],
    }


def window_variant_row(
    *,
    window: int,
    variant: RobustnessVariant,
    result: dict,
    signal_count: int,
) -> dict:
    metrics = result.get("metrics") or {}
    return {
        "window": int(window),
        "variant_id": variant.variant_id,
        "dimension": variant.dimension,
        "direction": variant.direction,
        "score_threshold": variant.score_threshold,
        "stop_loss": variant.stop_loss,
        "take_profit": variant.take_profit,
        "fee_rate": variant.fee_rate,
        "slippage_multiplier": variant.slippage_multiplier,
        "signal_count": int(signal_count),
        "closed_trades": int(metrics.get("closed_trades") or 0),
        "total_return": float(metrics.get("total_return") or 0.0),
        "maximum_drawdown": float(metrics.get("maximum_drawdown") or 0.0),
        "run_id": (result.get("manifest") or {}).get("run_id"),
    }


def _mean_interval_95(values: pd.Series) -> list[float] | None:
    usable = pd.to_numeric(values, errors="coerce").dropna()
    if len(usable) < 2:
        return None
    mean = float(usable.mean())
    margin = 1.96 * float(usable.std(ddof=1)) / math.sqrt(len(usable))
    return [mean - margin, mean + margin]


def summarize_robustness_evaluation(
    rows: pd.DataFrame,
    *,
    policy: RobustnessEvaluationPolicy,
    variants: list[RobustnessVariant],
) -> dict:
    """Summarize local perturbations without choosing an outperforming rule."""

    empty = empty_robustness_evaluation(policy, variants)
    if rows.empty:
        return empty
    required = {
        "window",
        "variant_id",
        "signal_count",
        "closed_trades",
        "total_return",
        "maximum_drawdown",
    }
    missing = required.difference(rows.columns)
    if missing:
        raise ValueError(f"robustness rows missing columns: {sorted(missing)}")
    frame = rows.copy()
    variant_by_id = {variant.variant_id: variant for variant in variants}
    unknown = set(frame["variant_id"].astype(str)).difference(variant_by_id)
    if unknown:
        raise ValueError(f"unknown robustness variants: {sorted(unknown)}")
    summaries = []
    for variant in variants:
        group = frame[frame["variant_id"] == variant.variant_id]
        if group.empty:
            continue
        returns = pd.to_numeric(group["total_return"], errors="coerce").dropna()
        summaries.append(
            {
                **json_value(asdict(variant)),
                "validation_windows": int(group["window"].nunique()),
                "signal_count": int(group["signal_count"].sum()),
                "closed_trades": int(group["closed_trades"].sum()),
                "mean_window_return": (
                    None if returns.empty else float(returns.mean())
                ),
                "median_window_return": (
                    None if returns.empty else float(returns.median())
                ),
                "mean_window_return_ci95": _mean_interval_95(returns),
                "worst_window_return": (
                    None if returns.empty else float(returns.min())
                ),
                "mean_maximum_drawdown": float(
                    pd.to_numeric(
                        group["maximum_drawdown"], errors="coerce"
                    ).fillna(0.0).mean()
                ),
            }
        )
    baseline = next(
        (row for row in summaries if row["variant_id"] == "baseline"), None
    )
    if baseline is None:
        raise ValueError("baseline robustness result is required")
    baseline_return = baseline["mean_window_return"]
    baseline_trades = int(baseline["closed_trades"])
    baseline_windows = int(baseline["validation_windows"])
    assessed_variants = []
    for summary in summaries:
        variant_return = summary["mean_window_return"]
        delta = (
            None
            if baseline_return is None or variant_return is None
            else float(variant_return - baseline_return)
        )
        summary["delta_mean_return_vs_baseline"] = delta
        if summary["variant_id"] == "baseline":
            assessment = "reference_baseline"
        elif (
            baseline_windows < policy.minimum_validation_windows
            or baseline_trades < policy.minimum_completed_trades
            or int(summary["closed_trades"]) < policy.minimum_completed_trades
        ):
            assessment = "not_assessed_small_sample"
        elif (
            baseline_return is not None
            and variant_return is not None
            and baseline_return * variant_return < 0
        ):
            assessment = "fragile_sign_flip"
        elif delta is not None and abs(delta) >= max(
            policy.material_absolute_return_delta,
            abs(float(baseline_return)) * policy.material_relative_return_delta,
        ):
            assessment = "materially_sensitive"
        else:
            assessment = "no_material_fragility_detected"
        summary["fragility_assessment"] = assessment
        if summary["variant_id"] != "baseline":
            assessed_variants.append(summary)

    if (
        baseline_windows < policy.minimum_validation_windows
        or baseline_trades < policy.minimum_completed_trades
    ):
        overall = "not_assessed_small_sample"
    elif any(
        row["fragility_assessment"] == "fragile_sign_flip"
        for row in assessed_variants
    ):
        overall = "fragile_sign_flip"
    elif any(
        row["fragility_assessment"] == "materially_sensitive"
        for row in assessed_variants
    ):
        overall = "materially_sensitive"
    else:
        overall = "no_material_fragility_detected_within_tested_range"
    warnings = [
        "multiple_comparisons_can_create_false_impressions",
        "diagnostic_only_do_not_select_best_variant",
        "validation_windows_must_not_be_reused_for_rule_tuning",
    ]
    if overall == "not_assessed_small_sample":
        warnings.append("insufficient_sample_for_robustness_assessment")
    return {
        **empty,
        "overall_assessment": overall,
        "window_results": json_value(frame.to_dict(orient="records")),
        "summaries": json_value(summaries),
        "input_hash": frame_hash(
            frame,
            columns=[column for column in frame.columns if column != "run_id"],
        ),
        "warnings": warnings,
    }
