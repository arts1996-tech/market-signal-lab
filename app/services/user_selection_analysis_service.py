"""Freeze selected-universe analysis separately from virtual account execution."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.backtest.audit import stable_payload_hash
from app.database.models import (
    Asset,
    AssetAnalysisResult,
    AssetAnalysisRun,
    UserAssetSelection,
    UserAssetSelectionAnalysisResult,
    UserAssetSelectionAnalysisRun,
    UserAssetSelectionItem,
)
from app.services.asset_analysis_service import ASSET_ANALYSIS_NAME


SELECTED_UNIVERSE_ANALYSIS_VERSION = "selected-universe-analysis-snapshot-v1"


def _reason_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(reason) for reason in value]


def _frozen_analysis_result(source_result: dict | None) -> dict:
    """Standardize explainability without turning a snapshot into a trade decision."""

    if source_result is None:
        return {
            "source_result": {},
            "positive_reasons": [],
            "negative_reasons": ["元の全銘柄分析で品質ゲートを通過していません"],
            "trade_mode_eligibility": {
                "cash": "unavailable_due_to_insufficient_data",
                "margin_long": "not_implemented",
                "margin_short": "not_implemented",
                "auto_select": "not_implemented",
            },
            "human_review_required": True,
        }
    return {
        "source_result": source_result,
        "positive_reasons": _reason_list(source_result.get("attention_reasons"))
        + _reason_list(source_result.get("movement_reasons")),
        "negative_reasons": _reason_list(source_result.get("quality_warnings"))
        + _reason_list(source_result.get("metric_quality_reasons")),
        "trade_mode_eligibility": {
            "cash": "not_assessed",
            "margin_long": "not_implemented",
            "margin_short": "not_implemented",
            "auto_select": "not_implemented",
        },
        "human_review_required": True,
    }


def build_selection_analysis_rows(
    selection_items: list[dict], source_results: list[dict]
) -> list[dict]:
    """Return one frozen row per selected asset, including quality-gated omissions."""

    source_by_asset = {str(row["asset_id"]): row for row in source_results}
    rows = []
    for item in selection_items:
        asset_id = str(item["asset_id"])
        source = source_by_asset.get(asset_id)
        if source is None:
            payload = {
                "asset_id": asset_id,
                "source_asset_analysis_result_id": None,
                "analysis_status": "insufficient_data",
                "data_as_of": None,
                "observations": None,
                "quality_reasons": ["not_eligible_in_source_analysis_run"],
                "result": _frozen_analysis_result(None),
            }
        else:
            payload = {
                "asset_id": asset_id,
                "source_asset_analysis_result_id": str(source["id"]),
                "analysis_status": "analyzed",
                "data_as_of": source["data_as_of"],
                "observations": int(source["observations"]),
                "quality_reasons": [],
                "result": _frozen_analysis_result(source["result"]),
            }
        payload["input_hash"] = stable_payload_hash(payload)
        rows.append(payload)
    return rows


def _source_result_payload(result: AssetAnalysisResult) -> dict:
    return {
        "id": result.id,
        "asset_id": result.asset_id,
        "data_as_of": result.data_as_of,
        "observations": result.observations,
        "result": result.result or {},
    }


def snapshot_selected_universe_analysis(
    session: Session,
    *,
    selection_id: str,
    source_asset_analysis_run_id: str,
) -> tuple[UserAssetSelectionAnalysisRun, bool]:
    """Freeze one existing all-universe analysis for exactly one active selection version."""

    selection = session.get(UserAssetSelection, selection_id)
    if selection is None:
        raise ValueError("user asset selection does not exist")
    if selection.status != "active":
        raise ValueError("inactive asset selection cannot start a new analysis snapshot")
    latest_version = session.scalar(
        select(func.max(UserAssetSelection.version)).where(
            UserAssetSelection.selection_key == selection.selection_key
        )
    )
    if selection.version != latest_version:
        raise ValueError("only the latest asset selection version can start a new analysis snapshot")
    source_run = session.get(AssetAnalysisRun, source_asset_analysis_run_id)
    if source_run is None:
        raise ValueError("source asset analysis run does not exist")
    if source_run.analysis_name != ASSET_ANALYSIS_NAME:
        raise ValueError("source run is not a supported asset analysis run")

    existing = session.scalar(
        select(UserAssetSelectionAnalysisRun).where(
            UserAssetSelectionAnalysisRun.selection_id == selection.id,
            UserAssetSelectionAnalysisRun.source_asset_analysis_run_id == source_run.id,
        )
    )
    if existing is not None:
        return existing, False

    selected_items = list(
        session.execute(
            select(UserAssetSelectionItem, Asset)
            .join(Asset, Asset.id == UserAssetSelectionItem.asset_id)
            .where(UserAssetSelectionItem.selection_id == selection.id)
            .order_by(UserAssetSelectionItem.display_order)
        ).all()
    )
    if not selected_items:
        raise ValueError("asset selection has no items")
    selection_rows = [
        {"asset_id": item.asset_id, "symbol": asset.symbol, "exchange": asset.exchange or ""}
        for item, asset in selected_items
    ]
    source_results = list(
        session.scalars(
            select(AssetAnalysisResult).where(
                AssetAnalysisResult.run_id == source_run.id,
                AssetAnalysisResult.asset_id.in_([item["asset_id"] for item in selection_rows]),
            )
        ).all()
    )
    rows = build_selection_analysis_rows(
        selection_rows, [_source_result_payload(result) for result in source_results]
    )
    analyzed_count = sum(row["analysis_status"] == "analyzed" for row in rows)
    status = "success" if analyzed_count == len(rows) else "partial" if analyzed_count else "insufficient_data"
    snapshot_hash = stable_payload_hash(
        {
            "selection_key": selection.selection_key,
            "selection_version": selection.version,
            "selection_composition_hash": selection.composition_hash,
            "source_asset_analysis_run_id": source_run.id,
            "analysis_rule_version": source_run.rule_version,
            "input_data_version": source_run.input_data_version,
            "rows": rows,
            "snapshot_version": SELECTED_UNIVERSE_ANALYSIS_VERSION,
        }
    )
    run = UserAssetSelectionAnalysisRun(
        selection_id=selection.id,
        selection_key=selection.selection_key,
        selection_version=selection.version,
        selection_composition_hash=selection.composition_hash,
        source_asset_analysis_run_id=source_run.id,
        data_scope=source_run.data_scope,
        analysis_rule_version=source_run.rule_version,
        source_policy_version=source_run.source_policy_version,
        input_data_version=source_run.input_data_version,
        snapshot_hash=snapshot_hash,
        data_as_of=source_run.data_as_of,
        status=status,
    )
    session.add(run)
    session.flush()
    for row in rows:
        session.add(
            UserAssetSelectionAnalysisResult(
                run_id=run.id,
                asset_id=row["asset_id"],
                source_asset_analysis_result_id=row["source_asset_analysis_result_id"],
                analysis_status=row["analysis_status"],
                data_as_of=row["data_as_of"],
                observations=row["observations"],
                input_hash=row["input_hash"],
                quality_reasons=row["quality_reasons"],
                result=row["result"],
            )
        )
    session.flush()
    return run, True
