"""Shared Tokyo-session scheduling rules for forward-account jobs."""

from __future__ import annotations

from datetime import time

import pandas as pd

from app.analysis.market_calendar import is_exchange_session


def parse_jst_cutoff(value: str | None) -> time | None:
    if value is None:
        return None
    try:
        hour_text, minute_text = value.split(":", maxsplit=1)
        return time(hour=int(hour_text), minute=int(minute_text))
    except (TypeError, ValueError) as exc:
        raise ValueError("not-before-jst must use HH:MM") from exc


def daily_schedule_reason(
    observed_at: pd.Timestamp, *, not_before_jst: str | None = None
) -> str | None:
    local = observed_at.tz_convert("Asia/Tokyo")
    local_date = local.normalize()
    if not is_exchange_session(local_date, "XTKS"):
        return f"{local_date.date()} is not a Tokyo Stock Exchange session"
    cutoff = parse_jst_cutoff(not_before_jst)
    if cutoff is not None and local.time().replace(tzinfo=None) < cutoff:
        return f"current JST time is before the {not_before_jst} recording cutoff"
    return None


def canonical_daily_decision_at(observed_at: pd.Timestamp) -> pd.Timestamp:
    """Use one stable 18:30 JST decision timestamp across same-day retries."""

    local = observed_at.tz_convert("Asia/Tokyo")
    canonical = local.normalize() + pd.Timedelta(hours=18, minutes=30)
    return canonical.tz_convert("UTC")
