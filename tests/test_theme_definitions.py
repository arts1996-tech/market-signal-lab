import json

import pytest
from pydantic import ValidationError

from app.analysis.theme_definitions import (
    THEME_DEFINITION_VERSION,
    ThemeDefinitionCatalog,
    load_theme_definition_catalog,
)


def test_initial_theme_definitions_are_loaded_as_declarative_versioned_inputs():
    catalog = load_theme_definition_catalog()

    assert catalog.definition_version == THEME_DEFINITION_VERSION
    assert {theme.identifier for theme in catalog.themes} == {
        "semiconductor", "ai_infrastructure", "defense", "gold", "robotics_fa",
        "electrification", "copper", "silver", "space", "cybersecurity",
        "stablecoin_tokenization", "high_dividend", "cash_flow",
    }
    assert next(theme for theme in catalog.themes if theme.identifier == "semiconductor").baseline_tier == "tier_1"
    assert {item.symbol for item in next(theme for theme in catalog.themes if theme.identifier == "silver").asset_memberships} == {"568A", "577A", "578A", "579A"}
    assert all(not theme.margin_trading_enabled for theme in catalog.themes)
    assert len(catalog.composition_hash()) == 64


def test_theme_definition_validation_rejects_duplicate_identifiers_and_unknown_fields():
    payload = load_theme_definition_catalog().model_dump(mode="json")
    payload["themes"].append(payload["themes"][0])

    with pytest.raises(ValidationError, match="identifiers must be unique"):
        ThemeDefinitionCatalog.model_validate(payload)

    payload = json.loads(json.dumps(load_theme_definition_catalog().model_dump(mode="json")))
    payload["themes"][0]["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ThemeDefinitionCatalog.model_validate(payload)
