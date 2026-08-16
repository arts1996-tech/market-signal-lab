import argparse
import json
from datetime import UTC, datetime

import pandas as pd
from app.analysis.fundamentals import normalize_financial_summary
from app.collectors.jquants import JQuantsClient
from app.core.logging import configure_logging
from app.database.models import Asset, FundamentalSnapshot
from app.database.session import SessionLocal
from sqlalchemy import select


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and validate one J-Quants financial summary."
    )
    parser.add_argument("--code", required=True)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    args = parser.parse_args()
    configure_logging()
    rows, latency_ms = JQuantsClient().fetch_financial_summary(
        args.code, args.from_date, args.to_date
    )
    normalized = normalize_financial_summary(rows.to_dict("records"))
    fetched_at = datetime.now(UTC)
    saved = 0
    with SessionLocal() as session:
        asset = session.scalar(select(Asset).where(Asset.symbol == args.code))
        if asset is not None:
            for row in normalized.to_dict("records"):
                for key, value in list(row.items()):
                    if pd.isna(value):
                        row[key] = None
                exists = session.scalar(select(FundamentalSnapshot).where(
                    FundamentalSnapshot.asset_id == asset.id,
                    FundamentalSnapshot.disclosed_at == row["disclosed_at"],
                    FundamentalSnapshot.period_end == row["period_end"],
                    FundamentalSnapshot.source == "jquants",
                ))
                if exists is None:
                    value_fields = [
                        "sales",
                        "operating_profit",
                        "net_income",
                        "eps",
                        "equity",
                        "total_assets",
                        "operating_cashflow",
                    ]
                    values = {key: row.get(key) for key in value_fields}
                    details = {
                        "provider": "jquants",
                        **values,
                        "book_value_per_share": row.get("book_value_per_share"),
                        "currency": row.get("currency"),
                        "unit": row.get("unit"),
                    }
                    session.add(
                        FundamentalSnapshot(
                            asset_id=asset.id,
                            disclosed_at=row["disclosed_at"],
                            period_end=row["period_end"],
                            source="jquants",
                            fetched_at=fetched_at,
                            details=details,
                            **values,
                        )
                    )
                    saved += 1
            session.commit()
    print(
        json.dumps(
            {
                "code": args.code,
                "raw_rows": len(rows),
                "valid_rows": len(normalized),
                "saved_rows": saved,
                "latency_ms": latency_ms,
                "fetched_at": fetched_at.isoformat(),
                "writes_database": saved > 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
