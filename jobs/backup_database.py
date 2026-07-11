from datetime import UTC, datetime, timedelta
from pathlib import Path
import os
import subprocess

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import record_job


def main() -> None:
    configure_logging()
    settings = get_settings()
    started_at = datetime.now(UTC)
    backup_dir = Path(settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    output = backup_dir / f"market_signal_lab_{started_at:%Y%m%d_%H%M%S}.dump"

    command = ["pg_dump", "--format=custom", "--file", str(output), settings.database_url]
    subprocess.run(command, check=True)

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

