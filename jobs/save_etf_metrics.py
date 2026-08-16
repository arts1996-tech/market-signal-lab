"""Persist provider-reported ETF metrics from a reviewed JSON payload."""

import argparse
import json
from pathlib import Path

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.phase3_collection_service import save_reviewed_etf_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and save ETF metrics JSON.")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--source", default="provider_reviewed")
    args = parser.parse_args()
    configure_logging()
    with SessionLocal() as session:
        result = save_reviewed_etf_metrics(session, args.file, args.source)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
