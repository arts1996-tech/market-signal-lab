from datetime import UTC, datetime, timedelta
from pathlib import Path
import os
import subprocess
import tempfile

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import record_job


def pg_dump_database_url(database_url: str) -> str:
    """Convert SQLAlchemy URLs to a libpq-compatible URL without logging it."""
    url = make_url(database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def pg_dump_command(database_url: str, output: Path) -> list[str]:
    return ["pg_dump", "--format=custom", "--file", str(output), pg_dump_database_url(database_url)]


def pg_dump_command_without_password(database_url: str, output: Path) -> tuple[list[str], str]:
    url = make_url(database_url)
    password = url.password or ""
    safe_url = url.set(drivername="postgresql", password="").render_as_string(hide_password=False)
    return ["pg_dump", "--format=custom", "--file", str(output), safe_url], password


def main() -> None:
    configure_logging()
    settings = get_settings()
    started_at = datetime.now(UTC)
    backup_dir = Path(settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    output = backup_dir / f"market_signal_lab_{started_at:%Y%m%d_%H%M%S}.dump"
    temporary_output = backup_dir / f".{output.name}.tmp"

    command, password = pg_dump_command_without_password(settings.database_url, temporary_output)
    url = make_url(settings.database_url)
    with tempfile.NamedTemporaryFile(mode="w", prefix="market-signal-lab-pgpass-", delete=False) as handle:
        pgpass = Path(handle.name)
        handle.write(
            f"{url.host or 'localhost'}:{url.port or 5432}:{url.database or ''}:{url.username or ''}:{password}\n"
        )
    pgpass.chmod(0o600)
    try:
        environment = os.environ.copy()
        environment["PGPASSFILE"] = os.fspath(pgpass)
        try:
            subprocess.run(command, check=True, env=environment)
            os.replace(temporary_output, output)
        except Exception as exc:
            temporary_output.unlink(missing_ok=True)
            with SessionLocal() as session:
                record_job(session, "backup_database", "error", started_at, {"error": str(exc)})
            raise
    finally:
        pgpass.unlink(missing_ok=True)

    cutoff = started_at - timedelta(days=settings.backup_retention_days)
    removed = 0
    for path in backup_dir.glob("market_signal_lab_*.dump"):
        if datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) < cutoff:
            path.unlink()
            removed += 1

    with SessionLocal() as session:
        record_job(
            session,
            "backup_database",
            "success",
            started_at,
            {"backup_file": os.fspath(output), "removed_old_files": removed},
        )
    print(f"Backup written: {output}")


if __name__ == "__main__":
    main()
