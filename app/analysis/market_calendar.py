from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd


@lru_cache(maxsize=4)
def exchange_calendar(name: str):
    return xcals.get_calendar(name)


def is_exchange_session(value: pd.Timestamp, calendar_name: str) -> bool:
    try:
        return exchange_calendar(calendar_name).is_session(_calendar_date(value))
    except (ValueError, xcals.errors.DateOutOfBounds):
        return False


def is_next_exchange_session(previous: pd.Timestamp, current: pd.Timestamp, calendar_name: str) -> bool:
    calendar = exchange_calendar(calendar_name)
    previous = _calendar_date(previous)
    current = _calendar_date(current)
    if not calendar.is_session(previous) or not calendar.is_session(current):
        return False
    try:
        sessions = calendar.sessions_in_range(previous, current)
    except (ValueError, xcals.errors.DateOutOfBounds):
        return False
    return len(sessions) == 2 and sessions[0] == previous and sessions[-1] == current


@lru_cache(maxsize=8192)
def next_exchange_session(value: pd.Timestamp, calendar_name: str) -> pd.Timestamp | None:
    calendar = exchange_calendar(calendar_name)
    try:
        return calendar.date_to_session(_calendar_date(value) + pd.Timedelta(days=1), direction="next")
    except (ValueError, xcals.errors.DateOutOfBounds):
        return None


def _calendar_date(value: pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def is_next_weekday(previous: pd.Timestamp, current: pd.Timestamp) -> bool:
    """Conservatively accept only consecutive weekday observations.

    Exchange holiday calendars are introduced in a later task.  A gap spanning
    an unknown weekday is excluded instead of being mislabeled as one session.
    """
    previous = pd.Timestamp(previous).normalize()
    current = pd.Timestamp(current).normalize()
    return current == previous + pd.offsets.BDay(1)


def consecutive_weekday_returns(close: pd.Series) -> pd.Series:
    ordered = close.dropna().sort_index().copy()
    ordered.index = pd.to_datetime(ordered.index, utc=True).normalize()
    ordered = ordered[~ordered.index.duplicated(keep="last")]
    values = ordered.pct_change(fill_method=None)
    valid = [False]
    valid.extend(
        is_next_weekday(previous, current)
        for previous, current in zip(ordered.index[:-1], ordered.index[1:], strict=True)
    )
    return values.where(valid).dropna()


def calendar_gap_report(observed_dates, calendar_name: str) -> dict:
    """Report missing and non-session dates against the pinned exchange calendar."""
    observed = {_calendar_date(pd.Timestamp(value)) for value in observed_dates}
    if not observed:
        return {"calendar": calendar_name, "missing_sessions": [], "unexpected_sessions": []}
    start, end = min(observed), max(observed)
    expected = {
        _calendar_date(value)
        for value in exchange_calendar(calendar_name).sessions_in_range(start, end)
    }
    return {
        "calendar": calendar_name,
        "missing_sessions": sorted(expected - observed),
        "unexpected_sessions": sorted(observed - expected),
    }


def latest_contiguous_exchange_observations(observed_dates, calendar_name: str) -> int:
    """Count the latest uninterrupted exchange-session suffix."""
    ordered = sorted({_calendar_date(pd.Timestamp(value)) for value in observed_dates})
    if not ordered or not is_exchange_session(ordered[-1], calendar_name):
        return 0
    count = 1
    for previous, current in zip(reversed(ordered[:-1]), reversed(ordered[1:]), strict=True):
        if not is_next_exchange_session(previous, current, calendar_name):
            break
        count += 1
    return count


def align_us_previous_to_japan(
    us_returns: pd.Series,
    japan_returns: pd.Series,
    us_calendar: str | None = None,
    japan_calendar: str | None = None,
) -> pd.DataFrame:
    """Map each Japan trading day to the latest available earlier US trading day."""
    us = us_returns.dropna().sort_index()
    jp = japan_returns.dropna().sort_index()
    rows = []
    us_dates = list(us.index)
    japan_calendar_start = None
    if us_calendar and japan_calendar:
        japan_calendar_start = _calendar_date(exchange_calendar(japan_calendar).first_session)
    cursor = 0
    previous_japan_date = None
    for jp_date, jp_return in jp.items():
        while cursor < len(us_dates) and us_dates[cursor] < jp_date:
            cursor += 1
        if cursor == 0:
            continue
        us_date = us_dates[cursor - 1]
        if us_calendar and japan_calendar:
            if japan_calendar_start is not None and _calendar_date(us_date) < japan_calendar_start:
                continue
            expected_jp_date = next_exchange_session(us_date, japan_calendar)
            if expected_jp_date is None or _calendar_date(jp_date) != expected_jp_date or not is_exchange_session(us_date, us_calendar):
                continue
            component_dates = [
                candidate for candidate in us_dates
                if candidate <= us_date
                and (previous_japan_date is None or _calendar_date(candidate) >= _calendar_date(previous_japan_date))
            ]
            aligned_us_return = (1 + us.loc[component_dates]).prod() - 1
        elif not is_next_weekday(us_date, jp_date):
            continue
        else:
            aligned_us_return = us.loc[us_date]
        rows.append(
            {
                "japan_date": jp_date,
                "us_date": us_date,
                "us_return": aligned_us_return,
                "japan_return": jp_return,
            }
        )
        previous_japan_date = jp_date
    return pd.DataFrame(rows)
