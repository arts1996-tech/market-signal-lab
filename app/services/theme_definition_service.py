"""Persist validated theme-definition inputs without inventing asset records."""

from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.theme_definitions import ThemeDefinitionCatalog, load_theme_definition_catalog
from app.database.models import Asset, Theme, ThemeAssetMembership, ThemeVersion


def _theme_hash(theme) -> str:
    payload = json.dumps(theme.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def seed_theme_definitions(
    session: Session, catalog: ThemeDefinitionCatalog | None = None
) -> dict:
    """Create immutable theme versions only when every declared asset already exists."""

    catalog = catalog or load_theme_definition_catalog()
    expected = {
        membership.symbol
        for theme in catalog.themes
        for membership in theme.asset_memberships
    }
    assets = {
        asset.symbol: asset
        for asset in session.scalars(select(Asset).where(Asset.symbol.in_(expected))).all()
    }
    unresolved = sorted(expected - assets.keys())
    if unresolved:
        raise ValueError(f"theme seed assets are not registered: {', '.join(unresolved)}")

    created_themes = 0
    created_versions = 0
    created_memberships = 0
    for definition in catalog.themes:
        theme = session.scalar(select(Theme).where(Theme.identifier == definition.identifier))
        if theme is None:
            theme = Theme(identifier=definition.identifier, name=definition.name, status="active")
            session.add(theme)
            session.flush()
            created_themes += 1
        elif theme.name != definition.name:
            raise ValueError(f"theme name differs from persisted identifier: {definition.identifier}")

        composition_hash = _theme_hash(definition)
        version = session.scalar(
            select(ThemeVersion).where(
                ThemeVersion.theme_id == theme.id,
                ThemeVersion.composition_hash == composition_hash,
            )
        )
        if version is not None:
            continue
        version = ThemeVersion(
            theme_id=theme.id,
            baseline_tier=definition.baseline_tier,
            enabled=definition.enabled,
            margin_trading_enabled=definition.margin_trading_enabled,
            description=definition.description,
            effective_from=definition.effective_from,
            definition_version=catalog.definition_version,
            composition_hash=composition_hash,
            status="approved",
        )
        session.add(version)
        session.flush()
        created_versions += 1
        for membership in definition.asset_memberships:
            session.add(
                ThemeAssetMembership(
                    theme_version_id=version.id,
                    asset_id=assets[membership.symbol].id,
                    role=membership.role,
                    effective_from=membership.effective_from,
                    source_reference=membership.source_reference,
                    notes=membership.notes,
                )
            )
            created_memberships += 1
    return {
        "created_themes": created_themes,
        "created_versions": created_versions,
        "created_memberships": created_memberships,
        "composition_hash": catalog.composition_hash(),
    }
