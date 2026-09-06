"""Lunar phase, eight equal buckets, from the ephemeris."""
from __future__ import annotations
from ...ingest.celestial import lunar_state
from .base import result

def compute(dates, spec=None) -> dict:
    state = lunar_state(dates)
    return result(dates, state.phase_angle, state.phase_bucket())
