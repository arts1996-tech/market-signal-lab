from io import StringIO
import logging
from pathlib import Path

from app.core.config import Settings
from app.core.logging import SecretRedactionFilter
from app.core.security import REDACTED, redact_sensitive_text, register_secret
from app.services.market_service import concise_error_message


def test_repository_templates_do_not_contain_a_default_database_password():
    env_example = Path(".env.example").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    alembic = Path("alembic.ini").read_text(encoding="utf-8")

    assert "DATABASE_URL=\n" in env_example
    assert "POSTGRES_PASSWORD=\n" in env_example
    assert "market_password" not in env_example
    assert "market_password" not in compose
    assert "market_password" not in alembic
    assert "${POSTGRES_PASSWORD:?" in compose
    assert compose.count("${DATABASE_URL:?") == 3


def test_settings_repr_hides_database_url_and_api_keys():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://market:db-secret@localhost/lab",
        fred_api_key="fred-secret",
        jquants_api_key="jquants-secret",
    )

    rendered = repr(settings)

    assert "db-secret" not in rendered
    assert "fred-secret" not in rendered
    assert "jquants-secret" not in rendered
    assert settings.database_url.endswith("@localhost/lab")


def test_redactor_masks_urls_headers_bearer_tokens_and_registered_bare_values():
    register_secret("bare-runtime-secret")
    value = (
        "postgresql://market:db-secret@db/lab "
        'x-api-key="header-secret" '
        "Authorization: Bearer bearer-secret "
        "bare-runtime-secret"
    )

    rendered = redact_sensitive_text(value)

    for secret in ("db-secret", "header-secret", "bearer-secret", "bare-runtime-secret"):
        assert secret not in rendered
    assert rendered.count(REDACTED) >= 4
    assert "postgresql://market:" in rendered
    assert "@db/lab" in rendered


def test_logging_filter_masks_message_arguments_and_traceback_without_breaking_numbers():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(SecretRedactionFilter())
    logger = logging.Logger("security-test")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("rows=%d password=%s", 3, "log-secret")
    try:
        raise RuntimeError("Bearer traceback-secret")
    except RuntimeError:
        logger.exception("request failed with api_key=message-secret")

    rendered = stream.getvalue()
    assert "rows=3" in rendered
    for secret in ("log-secret", "traceback-secret", "message-secret"):
        assert secret not in rendered
    assert REDACTED in rendered


def test_concise_error_message_masks_credentials_before_database_or_ui_storage():
    error = RuntimeError(
        "failed postgresql://market:db-secret@db/lab with token=provider-secret"
    )

    rendered = concise_error_message(error)

    assert "db-secret" not in rendered
    assert "provider-secret" not in rendered
    assert rendered.startswith("RuntimeError:")
