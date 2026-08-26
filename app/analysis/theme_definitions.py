"""Validated, versionable theme-definition inputs for TH-P0.

Definitions live in data rather than Python constants.  TH-P1 will persist the
same validated shape in versioned database tables without changing the schema.
"""

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


THEME_DEFINITION_VERSION = "theme-definition-input-v1"
THEME_DEFINITION_PATH = Path(__file__).with_name("data") / "initial_themes.json"

ThemeTier = Literal["tier_1", "tier_2", "tier_3"]
ThemeRole = Literal["target_etf", "target_stock", "us_leader", "component", "proxy"]


class ThemeAssetMembershipInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(pattern=r"^[0-9]{3}[0-9A-Z]$")
    name: str = Field(min_length=1, max_length=255)
    asset_type: Literal["etf"] = "etf"
    market: Literal["jp"] = "jp"
    role: ThemeRole
    effective_from: date
    source_reference: str = Field(min_length=1, max_length=500)
    notes: str = Field(default="", max_length=500)


class ThemeDefinitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identifier: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=100)
    baseline_tier: ThemeTier
    enabled: bool = True
    margin_trading_enabled: bool = False
    effective_from: date
    description: str = Field(min_length=1, max_length=1000)
    asset_memberships: tuple[ThemeAssetMembershipInput, ...] = ()

    @field_validator("asset_memberships")
    @classmethod
    def memberships_are_unique(cls, values: tuple[ThemeAssetMembershipInput, ...]):
        keys = [(item.symbol, item.role, item.effective_from) for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("theme asset memberships must be unique within a definition")
        return values


class ThemeDefinitionCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_version: Literal["theme-definition-input-v1"]
    effective_from: date
    themes: tuple[ThemeDefinitionInput, ...]

    @model_validator(mode="after")
    def identifiers_are_unique(self):
        identifiers = [theme.identifier for theme in self.themes]
        if not identifiers:
            raise ValueError("at least one theme definition is required")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("theme identifiers must be unique")
        if any(theme.effective_from < self.effective_from for theme in self.themes):
            raise ValueError("theme effective_from must not predate catalog effective_from")
        return self

    def composition_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return sha256(canonical.encode("utf-8")).hexdigest()


def load_theme_definition_catalog(path: Path | None = None) -> ThemeDefinitionCatalog:
    """Load declarative input without writing it to the database."""

    payload = json.loads((path or THEME_DEFINITION_PATH).read_text(encoding="utf-8"))
    return ThemeDefinitionCatalog.model_validate(payload)
