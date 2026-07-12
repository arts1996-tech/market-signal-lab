"""Compare stored observation dates with exchange_calendars sessions."""

import argparse
import json

from app.analysis.market_calendar import calendar_gap_report
from app.database.repositories import market_prices_frame
from app.database.session import SessionLocal


CALENDAR_BY_SOURCE = {"jquants": "XTKS", "fred": "XNYS"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check observed market dates against exchange calendars.")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    args = parser.parse_args()
    symbols = [value.strip() for value in args.symbols.split(",") if value.strip()]
    with SessionLocal() as session:
        prices = market_prices_frame(session, symbols, source_policy="real_only")
    reports = []
    if prices.empty:
        print("[]")
        return
    for symbol, group in prices.groupby("symbol"):
        source = str(group["source"].dropna().iloc[0]) if not group["source"].dropna().empty else ""
        calendar = CALENDAR_BY_SOURCE.get(source)
        if calendar:
            report = calendar_gap_report(group["price_time"], calendar)
            report["symbol"] = symbol
            report["source"] = source
            reports.append(report)
    print(json.dumps(reports, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
