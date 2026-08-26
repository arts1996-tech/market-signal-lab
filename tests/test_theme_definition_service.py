import pytest

from app.services.theme_definition_service import seed_theme_definitions


class _EmptyScalars:
    def all(self):
        return []


class _NoAssetSession:
    def scalars(self, _query):
        return _EmptyScalars()


def test_theme_seed_rejects_unregistered_assets_without_creating_placeholders():
    with pytest.raises(ValueError, match="1540") as error:
        seed_theme_definitions(_NoAssetSession())

    assert "610A" in str(error.value)
