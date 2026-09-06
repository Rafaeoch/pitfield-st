"""Lunar distance — the supermoon hypothesis, as a tercile of distance."""
from __future__ import annotations
from ...ingest.celestial import lunar_state
from .base import result, tercile

def compute(dates, spec=None) -> dict:
    state = lunar_state(dates)
    # Terciles of distance, so "low" is perigee: the closest, biggest moons.
    return result(dates, state.distance_km, tercile(state.distance_km))
