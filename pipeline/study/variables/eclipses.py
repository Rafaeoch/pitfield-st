"""Eclipses, as an event study.

Lunar eclipses come from Skyfield's eclipse solver, which is a real
computation. Solar eclipses have no solver in Skyfield, so they are identified
by the geometric condition that produces one: a new moon occurring while the
Moon is close enough to a node that its shadow reaches the Earth. That
criterion finds the dates reliably; it says nothing about where on Earth the
eclipse is visible, and it is labelled as a criterion rather than a solution.
"""
from __future__ import annotations
import numpy as np
from ...ingest.celestial import _ephemeris, _timescale
from .base import binary, result

# A solar eclipse occurs somewhere on Earth when the Moon is new and within
# roughly 1.5 degrees of ecliptic latitude.
SOLAR_LATITUDE_LIMIT = 1.5


def _lunar_eclipse_dates(start, end):
    from skyfield import eclipselib
    ts = _timescale()
    t0 = ts.utc(int(str(start)[:4]) - 1, 1, 1)
    t1 = ts.utc(int(str(end)[:4]) + 1, 1, 1)
    times, _, _ = eclipselib.lunar_eclipses(t0, t1, _ephemeris())
    return np.array([np.datetime64(s[:10], "D") for s in times.utc_iso()])


def _solar_eclipse_dates(start, end):
    from skyfield import almanac
    from skyfield.framelib import ecliptic_frame
    eph = _ephemeris(); ts = _timescale()
    t0 = ts.utc(int(str(start)[:4]) - 1, 1, 1)
    t1 = ts.utc(int(str(end)[:4]) + 1, 1, 1)
    times, phases = almanac.find_discrete(t0, t1, almanac.moon_phases(eph))
    new_moons = times[phases == 0]
    if len(new_moons) == 0:
        return np.array([], dtype="datetime64[D]")
    lat, _, _ = eph["earth"].at(new_moons).observe(eph["moon"]).apparent().frame_latlon(ecliptic_frame)
    close = np.abs(np.asarray(lat.degrees)) < SOLAR_LATITUDE_LIMIT
    return np.array([np.datetime64(s[:10], "D")
                     for s, keep in zip(new_moons.utc_iso(), close) if keep])


def compute(dates, spec=None) -> dict:
    from .base import event_window
    dates = np.asarray(dates, dtype="datetime64[D]")
    window = int((spec or {}).get("bucketing", {}).get("event_window_days", 5))
    finder = _solar_eclipse_dates if spec["id"] == "eclipses_solar" else _lunar_eclipse_dates
    events = finder(dates.min(), dates.max())
    flags = event_window(dates, events, window)
    return result(dates, flags.astype(float), binary(flags, "near_eclipse", "away"))
