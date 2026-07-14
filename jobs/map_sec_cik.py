"""Explicitly map one SEC ticker directory entry to an existing USD asset."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.analysis.sec_fundamentals import find_sec_cik, normalize_sec_ticker_directory
from app.core.logging import configure_logging
from app.database.models import Asset
from app.database.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Map one SEC ticker to an existing USD asset.")
    parser.add_argument("--json", required=True, help="SEC company_tickers_exchange.json path")
    parser.add_argument("--symbol", required=True)
    args = parser.parse_args()
    configure_logging()
    payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    directory = normalize_sec_ticker_directory(payload)
    cik = find_sec_cik(directory, args.symbol)
    if cik is None:
        raise SystemExit(f"SEC ticker not found uniquely: {args.symbol}")
    with SessionLocal() as session:
        asset = session.scalar(select(Asset).where(Asset.symbol == args.symbol))
        if asset is None:
            raise SystemExit(f"Asset not found: {args.symbol}")
        if asset.currency != "USD":
            raise SystemExit(f"Only USD assets may be mapped to SEC: {args.symbol}")
        conflict = session.scalar(select(Asset).where(Asset.sec_cik == cik, Asset.symbol != args.symbol))
        if conflict is not None:
            raise SystemExit(f"CIK already mapped to another asset: {conflict.symbol}")
        asset.sec_cik = cik
        session.commit()
    print(json.dumps({"symbol": args.symbol, "sec_cik": cik, "updated": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
