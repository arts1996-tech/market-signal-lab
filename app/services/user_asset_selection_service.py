"""Persist immutable versions of explicitly user-selected asset collections."""

from datetime import UTC, datetime

from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.user_selection import SelectionDraft, TickerInput
from app.database.models import Asset, UserAssetSelection, UserAssetSelectionItem


def _canonical_items(items: list[dict]) -> list[dict]:
    """Keep the user-selected order; it is part of the immutable composition."""

    return [
        {
            "asset_id": str(item["asset_id"]),
            "symbol": str(item["symbol"]),
            "exchange": str(item["exchange"]),
            "market": str(item["market"]),
        }
        for item in items
    ]


def _composition_hash(items: list[dict]) -> str:
    canonical = json.dumps(_canonical_items(items), sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _matches_immutable_version(
    selection: UserAssetSelection, draft: SelectionDraft, status: str
) -> bool:
    return (
        selection.name == draft.name
        and selection.created_by == draft.created_by
        and selection.effective_from == draft.effective_from
        and selection.rationale == draft.rationale
        and selection.status == status
    )


def _validated_items(session: Session, resolution: dict) -> list[dict]:
    if not resolution.get("valid"):
        raise ValueError("cannot persist an invalid asset selection")
    items = resolution.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("valid asset selection requires at least one item")
    try:
        canonical_items = _canonical_items(items)
    except (KeyError, TypeError) as error:
        raise ValueError("asset selection items are malformed") from error
    expected_hash = _composition_hash(canonical_items)
    if resolution.get("composition_hash") != expected_hash:
        raise ValueError("asset selection composition hash does not match items")

    asset_ids = [item["asset_id"] for item in canonical_items]
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("asset selection contains duplicate assets")
    persisted_assets = {
        str(asset.id): asset
        for asset in session.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()
    }
    if set(asset_ids) != set(persisted_assets):
        raise ValueError("asset selection references an unregistered asset")
    for item in canonical_items:
        asset = persisted_assets[item["asset_id"]]
        if (
            asset.symbol.upper() != item["symbol"].upper()
            or (asset.exchange or "").upper() != item["exchange"].upper()
        ):
            raise ValueError("asset selection item no longer matches its persisted asset")
    return canonical_items


def create_selection_version(
    session: Session,
    *,
    draft: SelectionDraft,
    resolution: dict,
    selection_key: str | None = None,
    status: str = "active",
) -> tuple[UserAssetSelection, bool]:
    """Append a version; never update a previously persisted selection or items."""

    if status not in {"active", "inactive"}:
        raise ValueError("selection status must be active or inactive")
    canonical_items = _validated_items(session, resolution)
    selection_key = selection_key or str(uuid4())
    if not selection_key or len(selection_key) > 64:
        raise ValueError("selection key must be between 1 and 64 characters")

    matching_versions = list(session.scalars(
        select(UserAssetSelection).where(
            UserAssetSelection.selection_key == selection_key,
            UserAssetSelection.composition_hash == resolution["composition_hash"],
        )
        .order_by(UserAssetSelection.version.desc())
    ))
    for matching_version in matching_versions:
        if _matches_immutable_version(matching_version, draft, status):
            return matching_version, False

    latest_existing = session.scalar(
        select(UserAssetSelection)
        .where(UserAssetSelection.selection_key == selection_key)
        .order_by(UserAssetSelection.version.desc())
        .limit(1)
    )
    if (
        latest_existing is not None
        and latest_existing.composition_hash == resolution["composition_hash"]
        and latest_existing.status == status
    ):
        raise ValueError("immutable selection composition already exists with different metadata")

    latest_version = session.scalar(
        select(func.max(UserAssetSelection.version)).where(
            UserAssetSelection.selection_key == selection_key
        )
    )
    selection = UserAssetSelection(
        selection_key=selection_key,
        version=(latest_version or 0) + 1,
        name=draft.name,
        created_by=draft.created_by,
        effective_from=draft.effective_from,
        status=status,
        rationale=draft.rationale,
        composition_hash=resolution["composition_hash"],
    )
    session.add(selection)
    session.flush()
    for display_order, item in enumerate(canonical_items, start=1):
        session.add(
            UserAssetSelectionItem(
                selection_id=selection.id,
                asset_id=item["asset_id"],
                display_order=display_order,
                status="active",
            )
        )
    session.flush()
    return selection, True


def deactivate_selection_version(
    session: Session,
    *,
    selection_id: str,
    created_by: str,
    rationale: str = "Liteで利用者が無効化",
    effective_from: datetime | None = None,
) -> tuple[UserAssetSelection, bool]:
    """Append an inactive version; never mutate or delete the active version."""

    selection = session.get(UserAssetSelection, selection_id)
    if selection is None:
        raise ValueError("user asset selection does not exist")
    latest_version = session.scalar(
        select(func.max(UserAssetSelection.version)).where(
            UserAssetSelection.selection_key == selection.selection_key
        )
    )
    if selection.version != latest_version:
        raise ValueError("only the latest asset selection version can be deactivated")
    if selection.status != "active":
        raise ValueError("asset selection is already inactive")
    if not created_by or len(created_by) > 100:
        raise ValueError("created_by must be between 1 and 100 characters")

    rows = list(
        session.execute(
            select(UserAssetSelectionItem, Asset)
            .join(Asset, Asset.id == UserAssetSelectionItem.asset_id)
            .where(UserAssetSelectionItem.selection_id == selection.id)
            .order_by(UserAssetSelectionItem.display_order)
        ).all()
    )
    if not rows:
        raise ValueError("asset selection has no items")
    items = [
        {
            "asset_id": item.asset_id,
            "symbol": asset.symbol,
            "exchange": asset.exchange or "",
            "market": "jp" if (asset.exchange or "").upper() == "JPX" else "us",
        }
        for item, asset in rows
    ]
    draft = SelectionDraft(
        name=selection.name,
        created_by=created_by,
        effective_from=effective_from or datetime.now(UTC),
        rationale=rationale,
        tickers=tuple(
            TickerInput(
                market=item["market"],
                exchange=item["exchange"],
                symbol=item["symbol"],
            )
            for item in items
        ),
    )
    # The persisted assets above are the authoritative resolution. Constructing a
    # status-only version must not re-resolve or infer different symbols.
    resolution = {
        "valid": True,
        "items": items,
        "errors": [],
        "composition_hash": _composition_hash(items),
    }
    return create_selection_version(
        session,
        draft=draft,
        resolution=resolution,
        selection_key=selection.selection_key,
        status="inactive",
    )
