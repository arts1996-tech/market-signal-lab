"""Measure one J-Quants period request without writing to the database."""

import argparse
import json
from datetime import UTC, datetime

from app.collectors.jquants import JQuantsClient
from app.core.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure a small J-Quants period request.")
    parser.add_argument("--code", required=True)
    parser.add_argument("--from-date", required=True, help="YYYYMMDD")
    parser.add_argument("--to-date", required=True, help="YYYYMMDD")
    args = parser.parse_args()
    configure_logging()
    started_at = datetime.now(UTC)
    bars, latency_ms = JQuantsClient().fetch_daily_bars(
        args.code, from_date=args.from_date, to_date=args.to_date
    )
    print(json.dumps({
        "code": args.code,
        "from_date": args.from_date,
        "to_date": args.to_date,
        "rows": len(bars),
        "latency_ms": latency_ms,
        "measured_at": started_at.isoformat(),
        "writes_database": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
