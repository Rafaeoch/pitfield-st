"""US daylight saving transitions, from the real tz database."""
from __future__ import annotations
from datetime import date, timedelta
from zoneinfo import ZoneInfo
import numpy as np
from .base import binary, event_window, result

TZ = ZoneInfo("America/New_York")

def _transitions(start: date, end: date):
    """Days where New York's UTC offset changes. No hardcoded rules."""
    out, day = [], start - timedelta(days=1)
    prev = None
    while day <= end:
        offset = TZ.utcoffset(__import__("datetime").datetime(day.year, day.month, day.day, 12))
        if prev is not None and offset != prev:
            out.append(np.datetime64(day.isoformat(), "D"))
        prev = offset
        day += timedelta(days=1)
    return np.array(out, dtype="datetime64[D]")

def compute(dates, spec=None) -> dict:
    dates = np.asarray(dates, dtype="datetime64[D]")
    window = int((spec or {}).get("bucketing", {}).get("event_window_days", 3))
    events = _transitions(dates.min().astype(object), dates.max().astype(object))
    flags = event_window(dates, events, window)
    return result(dates, flags.astype(float), binary(flags, "near_change", "away"))
