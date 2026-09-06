"""Position within the Metonic cycle: 6939.688 days, 19 tropical years."""
from __future__ import annotations
from .base import cycle_phase, result, tercile

METONIC_DAYS = 6939.688

def compute(dates, spec=None) -> dict:
    phase = cycle_phase(dates, METONIC_DAYS)
    return result(dates, phase, tercile(phase))
