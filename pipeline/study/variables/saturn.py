"""Saturn's ecliptic longitude — the 29.5-year orbit behind the 'return'."""
from __future__ import annotations
from ...ingest.celestial import ecliptic_longitude
from .base import result, tercile

def compute(dates, spec=None) -> dict:
    lon = ecliptic_longitude("saturn", dates)
    return result(dates, lon, tercile(lon))
