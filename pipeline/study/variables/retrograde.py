"""Apparent retrograde motion, per planet, from real apparent longitudes."""
from __future__ import annotations
from ...ingest.celestial import is_retrograde
from .base import binary, result

PLANET_FOR = {
    "mercury_retrograde": "mercury",
    "venus_retrograde": "venus",
    "mars_retrograde": "mars",
}

def compute(dates, spec=None) -> dict:
    planet = PLANET_FOR[spec["id"]]
    flags = is_retrograde(planet, dates)
    return result(dates, flags.astype(float), binary(flags, "retrograde", "direct"))
