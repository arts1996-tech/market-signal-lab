import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.security import redact_sensitive_text


class SecretRedactionFilter(logging.Filter):
    """Redact messages, arguments and tracebacks before any handler emits them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            rendered = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact_sensitive_text(rendered)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_sensitive_text(record.exc_text)
        return True


def _ensure_redaction(handler: logging.Handler) -> None:
    if not any(isinstance(item, SecretRedactionFilter) for item in handler.filters):
        handler.addFilter(SecretRedactionFilter())


def configure_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            _ensure_redaction(handler)
        return

    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    _ensure_redaction(console)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "market-signal-lab.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    _ensure_redaction(file_handler)
    root.addHandler(file_handler)
