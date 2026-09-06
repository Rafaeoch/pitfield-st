"""Hours of night at 40 degrees north — the Kamstra, Kramer & Levi variable.

Computed from the Sun's declination rather than a day-of-year table, so the
obliquity and the equation of centre are the real ones.
"""
from __future__ import annotations
import numpy as np
from ...ingest.celestial import _ephemeris, _to_time
from .base import result, tercile

LATITUDE = 40.0     # a stand-in for the latitude of the exchanges

def compute(dates, spec=None) -> dict:
    from skyfield.framelib import ecliptic_frame
    eph = _ephemeris()
    t = _to_time(dates)
    lat_b, lon_b, _ = eph["earth"].at(t).observe(eph["sun"]).apparent().frame_latlon(ecliptic_frame)
    lam = np.radians(np.asarray(lon_b.degrees))
    obliquity = np.radians(23.4392911)
    dec = np.arcsin(np.sin(obliquity) * np.sin(lam))

    phi = np.radians(LATITUDE)
    cos_h = -np.tan(phi) * np.tan(dec)
    cos_h = np.clip(cos_h, -1.0, 1.0)
    daylight_hours = 2.0 * np.degrees(np.arccos(cos_h)) / 15.0
    night = 24.0 - daylight_hours
    return result(dates, night, tercile(night))
