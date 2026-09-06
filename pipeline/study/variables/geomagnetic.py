"""Geomagnetic storms — Kp >= 7, the Krivelyova & Robotti threshold."""
from __future__ import annotations
import numpy as np
from ...ingest.geomagnetic import STORM_THRESHOLD, load_kp
from .base import binary, result

def compute(dates, spec=None) -> dict:
    series = load_kp()
    dates = np.asarray(dates, dtype="datetime64[D]")
    lookup = dict(zip(series.dates.tolist(), series.kp_max.tolist()))
    values = np.array([lookup.get(d, np.nan) for d in dates.tolist()], dtype=float)
    threshold = float((spec or {}).get("bucketing", {}).get("threshold", {}).get("kp", STORM_THRESHOLD))
    storm = np.where(np.isfinite(values), values >= threshold, False)
    return result(dates, values, binary(storm, "storm", "quiet"))
