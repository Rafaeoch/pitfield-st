"""Shared bucketing. Every variable module returns the same shape.

    compute(dates, spec) -> {"date": ndarray, "value": ndarray, "bucket": ndarray}

The bucketing rules are fixed in the pre-registration and implemented once,
here, so no hypothesis can quietly acquire its own. Re-bucketing until something
appears is the most common way a study like this becomes a fishing expedition,
and the defence against it is that there is only one place to change.
"""

from __future__ import annotations

import numpy as np

TERCILE_LABELS = ("low", "mid", "high")


def tercile(values: np.ndarray) -> np.ndarray:
    """Split at terciles of the variable's own full-sample distribution."""
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, "unknown", dtype=object)
    finite = np.isfinite(v)
    if finite.sum() < 3:
        return out.astype(str)
    lo, hi = np.percentile(v[finite], [100 / 3, 200 / 3])
    out[finite & (v <= lo)] = TERCILE_LABELS[0]
    out[finite & (v > lo) & (v <= hi)] = TERCILE_LABELS[1]
    out[finite & (v > hi)] = TERCILE_LABELS[2]
    return out.astype(str)


def binary(flags: np.ndarray, on: str = "on", off: str = "off") -> np.ndarray:
    return np.where(np.asarray(flags, dtype=bool), on, off)


def event_window(
    dates: np.ndarray, event_dates, window: int = 5
) -> np.ndarray:
    """Mark every session within +/- ``window`` days of an event.

    The window is in calendar days around the event, then intersected with the
    session dates we actually have. Counting in trading days instead would make
    a window straddling a holiday weekend wider in real time than one that does
    not, which is the sort of asymmetry that manufactures effects.
    """
    dates = np.asarray(dates, dtype="datetime64[D]")
    flags = np.zeros(dates.shape, dtype=bool)
    for e in np.asarray(list(event_dates), dtype="datetime64[D]"):
        flags |= np.abs((dates - e).astype(int)) <= window
    return flags


def cycle_phase(dates: np.ndarray, period_days: float, epoch: str = "2000-01-01") -> np.ndarray:
    """Position within a fixed-length cycle, as a fraction in [0, 1)."""
    d = np.asarray(dates, dtype="datetime64[D]")
    elapsed = (d - np.datetime64(epoch, "D")).astype(float)
    return np.mod(elapsed, period_days) / period_days


def result(dates, value, bucket) -> dict:
    return {
        "date": np.asarray(dates, dtype="datetime64[D]"),
        "value": np.asarray(value, dtype=float),
        "bucket": np.asarray(bucket, dtype=str),
    }
