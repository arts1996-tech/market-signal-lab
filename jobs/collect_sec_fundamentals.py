"""Fetch one SEC Company Facts payload and persist validated fundamentals."""

import argparse
import json
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select

from app.analysis.sec_fundamentals import normalize_sec_companyfacts
from app.collectors.sec import SecClient
from app.core.logging import configure_logging
from app.database.models import Asset, FundamentalSnapshot
from app.database.session import SessionLocal


def save_snapshots(session, symbol: str, frame: pd.DataFrame, fetched_at: datetime) -> int:
    asset = session.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        return 0
    saved = 0
    for row in frame.to_dict("records"):
        values = {
            key: None if pd.isna(row.get(key)) else row.get(key)
            for key in ["sales", "operating_profit", "net_income", "eps", "equity", "total_assets", "operating_cashflow"]
        }
        disclosed_at = row.get("disclosed_at")
        period_end = row.get("period_end")
        if disclosed_at is None or period_end is None:
            continue
        exists = session.scalar(select(FundamentalSnapshot).where(
            FundamentalSnapshot.asset_id == asset.id,
            FundamentalSnapshot.disclosed_at == disclosed_at,
            FundamentalSnapshot.period_end == period_end,
            FundamentalSnapshot.source == "sec_companyfacts",
        ))
        if exists is not None:
            continue
        session.add(FundamentalSnapshot(
            asset_id=asset.id,
            disclosed_at=disclosed_at,
            period_end=period_end,
            source="sec_companyfacts",
            fetched_at=fetched_at,
            details={"provider": "sec_companyfacts", **values, "book_value_per_share": row.get("book_value_per_share")},
            **values,
        ))
        saved += 1
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch one SEC Company Facts payload.")
    parser.add_argument("--cik", required=True, help="10-digit SEC CIK")
    parser.add_argument("--symbol", required=True, help="Existing asset symbol to attach the facts to")
    args = parser.parse_args()
    configure_logging()
    payload, latency_ms = SecClient().fetch_companyfacts(args.cik)
    normalized = normalize_sec_companyfacts(payload, symbol=args.symbol)
    fetched_at = datetime.now(UTC)
    with SessionLocal() as session:
        saved = save_snapshots(session, args.symbol, normalized, fetched_at)
        session.commit()
    print(json.dumps({
        "symbol": args.symbol,
        "cik": str(args.cik).zfill(10),
        "valid_rows": len(normalized),
        "saved_rows": saved,
        "latency_ms": latency_ms,
        "fetched_at": fetched_at.isoformat(),
        "writes_database": saved > 0,
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
