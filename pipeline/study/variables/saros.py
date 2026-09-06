"""Position within the Saros cycle: 6585.3211 days, or 18y 11d 8h."""
from __future__ import annotations
from .base import cycle_phase, result, tercile

SAROS_DAYS = 6585.3211

def compute(dates, spec=None) -> dict:
    phase = cycle_phase(dates, SAROS_DAYS)
    return result(dates, phase, tercile(phase))
