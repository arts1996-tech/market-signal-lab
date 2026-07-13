"""Persist provider-reported ETF metrics from a reviewed JSON payload."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.analysis.etf_metrics import normalize_etf_metrics
from app.database.models import Asset, EtfMetricSnapshot
from app.database.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and save ETF metrics JSON.")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--source", default="provider_reviewed")
    args = parser.parse_args()
    rows = json.loads(args.file.read_text(encoding="utf-8"))
    normalized = normalize_etf_metrics(rows if isinstance(rows, list) else rows.get("items", []))
    saved = 0
    with SessionLocal() as session:
        for row in normalized.to_dict("records"):
            asset = session.scalar(select(Asset).where(Asset.symbol == row["symbol"], Asset.asset_type == "etf"))
            if asset is None:
                continue
            exists = session.scalar(select(EtfMetricSnapshot).where(EtfMetricSnapshot.asset_id == asset.id, EtfMetricSnapshot.observed_at == row["observed_at"], EtfMetricSnapshot.source == args.source))
            if exists is None:
                details = {key: value for key, value in row.items() if key not in {"symbol", "observed_at"}}
                session.add(EtfMetricSnapshot(asset_id=asset.id, observed_at=row["observed_at"], source=args.source, details=details, fetched_at=datetime.now(UTC)))
                saved += 1
        session.commit()
    print(json.dumps({"valid_rows": len(normalized), "saved_rows": saved}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
