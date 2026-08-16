import argparse
import json

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.phase3_collection_service import collect_jquants_financial_summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and validate one J-Quants financial summary."
    )
    parser.add_argument("--code", required=True)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    args = parser.parse_args()
    configure_logging()
    with SessionLocal() as session:
        result = collect_jquants_financial_summary(
            session, args.code, args.from_date, args.to_date
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
