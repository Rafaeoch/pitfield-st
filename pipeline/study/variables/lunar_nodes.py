"""Longitude of the mean ascending node — the 18.6-year cycle."""
from __future__ import annotations
from ...ingest.celestial import lunar_state
from .base import result, tercile

def compute(dates, spec=None) -> dict:
    state = lunar_state(dates)
    return result(dates, state.node_longitude, tercile(state.node_longitude))
