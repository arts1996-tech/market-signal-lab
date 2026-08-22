from dataclasses import asdict

import pandas as pd
import pytest

from app.backtest.robustness_evaluation import (
    RobustnessEvaluationPolicy,
    build_robustness_variants,
    summarize_robustness_evaluation,
)


def _variants():
    return build_robustness_variants(
        score_threshold=70,
        stop_loss=-0.05,
        take_profit=0.08,
        fee_rate=0.001,
    )


def test_robustness_grid_changes_one_parameter_at_a_time_without_ranking():
    variants = _variants()
    baseline = asdict(variants[0])
    parameter_names = {
        "score_threshold",
        "stop_loss",
        "take_profit",
        "fee_rate",
        "slippage_multiplier",
    }

    assert variants[0].variant_id == "baseline"
    assert len(variants) == 11
    for variant in variants[1:]:
        values = asdict(variant)
        changed = {
            name for name in parameter_names if values[name] != baseline[name]
        }
        expected = (
            "slippage_multiplier"
            if variant.dimension == "slippage"
            else variant.dimension
        )
        assert changed == {expected}


def test_small_sample_is_not_reported_as_robust_or_optimized():
    variants = _variants()
    rows = pd.DataFrame(
        [
            {
                "window": window,
                "variant_id": variant.variant_id,
                "signal_count": 1,
                "closed_trades": 1,
                "total_return": 0.01,
                "maximum_drawdown": -0.01,
            }
            for window in (1, 2)
            for variant in variants
        ]
    )

    result = summarize_robustness_evaluation(
        rows, policy=RobustnessEvaluationPolicy(), variants=variants
    )

    assert result["overall_assessment"] == "not_assessed_small_sample"
    assert result["parameter_selection_performed"] is False
    assert result["selected_best_variant"] is None
    assert "multiple_comparisons_can_create_false_impressions" in result["warnings"]
    assert all(
        summary["fragility_assessment"] == "not_assessed_small_sample"
        for summary in result["summaries"]
        if summary["variant_id"] != "baseline"
    )


def test_sign_flip_under_small_perturbation_is_classified_as_fragile():
    variants = _variants()[:2]
    rows = []
    for window in (1, 2, 3):
        for variant in variants:
            rows.append(
                {
                    "window": window,
                    "variant_id": variant.variant_id,
                    "signal_count": 12,
                    "closed_trades": 10,
                    "total_return": (
                        0.01 if variant.variant_id == "baseline" else -0.01
                    ),
                    "maximum_drawdown": -0.02,
                }
            )

    result = summarize_robustness_evaluation(
        pd.DataFrame(rows),
        policy=RobustnessEvaluationPolicy(),
        variants=variants,
    )
    changed = next(
        row for row in result["summaries"] if row["variant_id"] != "baseline"
    )

    assert result["overall_assessment"] == "fragile_sign_flip"
    assert changed["fragility_assessment"] == "fragile_sign_flip"
    assert changed["delta_mean_return_vs_baseline"] == pytest.approx(-0.02)


def test_run_id_does_not_change_reproducible_robustness_input_hash():
    variants = _variants()[:2]
    base_rows = pd.DataFrame(
        [
            {
                "window": window,
                "variant_id": variant.variant_id,
                "signal_count": 12,
                "closed_trades": 10,
                "total_return": 0.01,
                "maximum_drawdown": -0.02,
                "run_id": f"first-{window}-{variant.variant_id}",
            }
            for window in (1, 2, 3)
            for variant in variants
        ]
    )
    repeated_rows = base_rows.copy()
    repeated_rows["run_id"] = repeated_rows["run_id"].str.replace(
        "first", "repeated"
    )

    first = summarize_robustness_evaluation(
        base_rows, policy=RobustnessEvaluationPolicy(), variants=variants
    )
    repeated = summarize_robustness_evaluation(
        repeated_rows, policy=RobustnessEvaluationPolicy(), variants=variants
    )

    assert repeated["input_hash"] == first["input_hash"]
