"""Explicitly import an allowlisted set of US stock assets from SEC directory JSON."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.analysis.sec_fundamentals import find_sec_cik, normalize_sec_ticker_directory
from app.core.logging import configure_logging
from app.database.models import Asset
from app.database.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Import selected US stocks from SEC ticker directory.")
    parser.add_argument("--json", required=True)
    parser.add_argument("--symbols", required=True, help="Comma-separated allowlist, e.g. AAPL,MSFT")
    args = parser.parse_args()
    configure_logging()
    directory = normalize_sec_ticker_directory(json.loads(Path(args.json).read_text(encoding="utf-8")))
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    results = []
    with SessionLocal() as session:
        for symbol in dict.fromkeys(symbols):
            row = directory[directory["symbol"] == symbol] if not directory.empty else directory
            cik = find_sec_cik(row, symbol)
            if cik is None:
                results.append({"symbol": symbol, "status": "not_found"})
                continue
            asset = session.scalar(select(Asset).where(Asset.symbol == symbol))
            if asset is not None:
                if asset.currency != "USD":
                    results.append({"symbol": symbol, "status": "conflict_non_usd"})
                    continue
                asset.sec_cik = cik
                results.append({"symbol": symbol, "status": "updated", "sec_cik": cik})
                continue
            session.add(Asset(
                symbol=symbol,
                name=str(row.iloc[0]["name"]),
                asset_type="stock",
                currency="USD",
                exchange="US",
                sec_cik=cik,
                source="sec",
                metadata_json={"provider": "sec_company_tickers_exchange"},
            ))
            results.append({"symbol": symbol, "status": "created", "sec_cik": cik})
        session.commit()
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
