"""Fetch one SEC Company Facts payload and persist validated fundamentals."""

import argparse
import json

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.phase3_collection_service import collect_sec_fundamentals


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch one SEC Company Facts payload.")
    parser.add_argument("--cik", required=True, help="10-digit SEC CIK")
    parser.add_argument("--symbol", required=True, help="Existing asset symbol to attach the facts to")
    args = parser.parse_args()
    configure_logging()
    with SessionLocal() as session:
        result = collect_sec_fundamentals(session, args.cik, args.symbol)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
