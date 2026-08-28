"""Secret registration and deterministic redaction for logs and error messages."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from urllib.parse import unquote


REDACTED = "[REDACTED]"
_REGISTERED_SECRETS: set[str] = set()
_SENSITIVE_ENV_SUFFIXES = ("API_KEY", "PASSWORD", "SECRET", "TOKEN", "AUTHORIZATION")
_URL_PASSWORD_PATTERN = re.compile(
    r"(?P<prefix>[a-z][a-z0-9+.-]*://[^\s:/@]+:)(?P<secret>[^\s/@]+)(?P<suffix>@)",
    re.IGNORECASE,
)
_NAMED_SECRET_PATTERN = re.compile(
    r"(?P<keyquote>[\"']?)(?P<name>api[_-]?key|x-api-key|access[_-]?token|"
    r"refresh[_-]?token|token|password|passwd|secret|authorization)(?P=keyquote)"
    r"(?P<separator>\s*[=:]\s*)"
    r"(?P<quote>[\"']?)(?P<secret>[^\s,;&\"'}]+)(?P=quote)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?P<prefix>\bBearer\s+)(?P<secret>[^\s,;]+)", re.IGNORECASE)


def register_secret(value: str | None) -> None:
    """Register a runtime secret so bare occurrences can also be redacted."""

    normalized = str(value or "")
    if len(normalized) >= 4 and normalized != REDACTED:
        _REGISTERED_SECRETS.add(normalized)


def register_database_url_secrets(database_url: str | None) -> None:
    """Register only the password component; retain non-secret DB diagnostics."""

    match = _URL_PASSWORD_PATTERN.search(str(database_url or ""))
    if match:
        encoded = match.group("secret")
        register_secret(encoded)
        register_secret(unquote(encoded))


def _environment_secrets() -> Iterable[str]:
    for name, value in os.environ.items():
        upper_name = name.upper()
        if any(
            upper_name == suffix or upper_name.endswith(f"_{suffix}")
            for suffix in _SENSITIVE_ENV_SUFFIXES
        ):
            if len(value) >= 4:
                yield value


def redact_sensitive_text(value: object) -> str:
    """Mask common credential forms and configured secret values in text."""

    text = str(value)
    text = _URL_PASSWORD_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{REDACTED}{match.group('suffix')}",
        text,
    )
    text = _BEARER_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{REDACTED}", text
    )
    text = _NAMED_SECRET_PATTERN.sub(
        lambda match: (
            f"{match.group('keyquote')}{match.group('name')}{match.group('keyquote')}"
            f"{match.group('separator')}"
            f"{match.group('quote')}{REDACTED}{match.group('quote')}"
        ),
        text,
    )
    secrets = set(_environment_secrets()) | _REGISTERED_SECRETS
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return text
