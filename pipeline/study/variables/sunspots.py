"""Daily total sunspot number from SILSO, aligned to session dates."""
from __future__ import annotations
import numpy as np
from ...ingest.solar import load_sunspots
from .base import result, tercile

def compute(dates, spec=None) -> dict:
    series = load_sunspots()
    dates = np.asarray(dates, dtype="datetime64[D]")
    lookup = dict(zip(series.dates.tolist(), series.number.tolist()))
    # No forward fill: a session with no sunspot observation is unknown, not
    # yesterday's value repeated.
    values = np.array([lookup.get(d, np.nan) for d in dates.tolist()], dtype=float)
    return result(dates, values, tercile(values))
