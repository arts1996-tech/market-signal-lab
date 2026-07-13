import argparse
import json
from datetime import UTC, datetime

from app.analysis.fundamentals import normalize_financial_summary
from app.collectors.jquants import JQuantsClient
from app.core.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and validate one J-Quants financial summary.")
    parser.add_argument("--code", required=True)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    args = parser.parse_args()
    configure_logging()
    rows, latency_ms = JQuantsClient().fetch_financial_summary(args.code, args.from_date, args.to_date)
    normalized = normalize_financial_summary(rows.to_dict("records"))
    print(json.dumps({"code": args.code, "raw_rows": len(rows), "valid_rows": len(normalized), "latency_ms": latency_ms, "fetched_at": datetime.now(UTC).isoformat(), "writes_database": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
