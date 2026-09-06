"""Real astronomy: positions, phases, aspects and retrogrades from JPL DE440s.

Everything here comes from the ephemeris. There is not a modulo-29.5 lunar phase
approximation anywhere in this file, and there must never be one. Two reasons,
one principled and one practical:

* A study whose whole point is statistical honesty cannot afford a variable that
  is quietly wrong by a day and a half at the extremes of its range.
* The moon page renders from this same code, so an approximation would be
  visible on screen as a terminator in the wrong place.

DE440s covers 1849-2150, which comfortably spans every series the study uses.

Angles are apparent geocentric ecliptic longitudes: what an observer on Earth
sees, including light-time and aberration. That matters for retrograde motion in
particular, which is a purely apparent phenomenon — no planet actually reverses
direction, it only appears to as Earth overtakes it. Using true heliocentric
positions would produce no retrogrades at all.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from skyfield.api import load, load_file
from skyfield.framelib import ecliptic_frame

KERNEL_PATH = Path("data/ephemeris/de440s.bsp")
KERNEL_URL = (
    "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp"
)

# DE440s addresses the outer planets by barycentre; the inner ones by body.
BODIES = {
    "mercury": "mercury barycenter",
    "venus": "venus barycenter",
    "mars": "mars barycenter",
    "jupiter": "jupiter barycenter",
    "saturn": "saturn barycenter",
    "uranus": "uranus barycenter",
    "neptune": "neptune barycenter",
    "pluto": "pluto barycenter",
}

# The classical aspects, with the orb the pre-registration fixes at +/-3 degrees.
ASPECTS = {
    "conjunction": 0.0,
    "sextile": 60.0,
    "square": 90.0,
    "trine": 120.0,
    "opposition": 180.0,
}
DEFAULT_ORB = 3.0

# Mean lunar distance bounds, for the perigee/apogee percentile.
LUNAR_PERIGEE_KM = 356_500
LUNAR_APOGEE_KM = 406_700


def ensure_kernel(path: Path = KERNEL_PATH) -> Path:
    """The ephemeris kernel must be present; we do not silently degrade."""
    if not path.exists():
        raise FileNotFoundError(
            f"ephemeris kernel missing at {path}. Fetch it with:\n"
            f"  curl -L {KERNEL_URL} -o {path}\n"
            "There is deliberately no approximation fallback."
        )
    return path


@functools.lru_cache(maxsize=1)
def _ephemeris():
    return load_file(str(ensure_kernel()))


@functools.lru_cache(maxsize=1)
def _timescale():
    return load.timescale()


def _to_time(dates):
    """numpy dates -> a Skyfield time array, evaluated at 00:00 UTC."""
    days = np.asarray(dates, dtype="datetime64[D]")
    years = days.astype("datetime64[Y]").astype(int) + 1970
    months = days.astype("datetime64[M]").astype(int) % 12 + 1
    day_of_month = (days - days.astype("datetime64[M]")).astype(int) + 1
    return _timescale().utc(years, months, day_of_month)


# Skyfield allocates on the order of half a gigabyte to observe a body over a
# fourteen-thousand-element time array. The study asks for the same bodies over
# the same dates many times — twenty-eight planet pairs alone means fifty-six
# requests for eight distinct bodies — so without a cache the run exhausts
# memory and is killed before it finishes. Keyed on the date array's bytes, so
# a different span is a different entry rather than a silent wrong answer.
_LONGITUDE_CACHE: dict[tuple, np.ndarray] = {}


def _cache_key(body: str, dates) -> tuple:
    d = np.asarray(dates, dtype="datetime64[D]")
    return (body, d.size, d.tobytes() if d.size < 200_000 else hash(d.tobytes()))


def ecliptic_longitude(body: str, dates) -> np.ndarray:
    """Apparent geocentric ecliptic longitude in degrees, 0-360."""
    key = _cache_key(body, dates)
    hit = _LONGITUDE_CACHE.get(key)
    if hit is not None:
        return hit

    eph = _ephemeris()
    target = eph[BODIES.get(body, body)]
    astrometric = eph["earth"].at(_to_time(dates)).observe(target).apparent()
    _, lon, _ = astrometric.frame_latlon(ecliptic_frame)
    out = np.asarray(lon.degrees) % 360.0
    _LONGITUDE_CACHE[key] = out
    return out


@dataclass(frozen=True)
class LunarState:
    dates: np.ndarray
    phase_angle: np.ndarray        # 0 = new, 180 = full
    illuminated: np.ndarray        # 0-1
    distance_km: np.ndarray
    distance_percentile: np.ndarray
    node_longitude: np.ndarray     # ascending node, degrees

    def phase_bucket(self, n: int = 8) -> np.ndarray:
        """Phase split into n equal-angle buckets, centred on the named phases.

        The offset of half a bucket matters: without it "new moon" would be the
        bucket *starting* at new rather than the one straddling it, and every
        phase label would sit half a bucket late.
        """
        width = 360.0 / n
        idx = np.floor(((self.phase_angle + width / 2) % 360.0) / width).astype(int)
        names8 = np.array([
            "new", "waxing_crescent", "first_quarter", "waxing_gibbous",
            "full", "waning_gibbous", "last_quarter", "waning_crescent",
        ])
        return names8[idx] if n == 8 else idx.astype(str)


def lunar_state(dates) -> LunarState:
    """Phase, illumination, distance and node longitude for each date."""
    eph = _ephemeris()
    t = _to_time(dates)
    earth = eph["earth"].at(t)

    _, moon_lon, moon_dist = earth.observe(eph["moon"]).apparent().frame_latlon(
        ecliptic_frame
    )
    _, sun_lon, _ = earth.observe(eph["sun"]).apparent().frame_latlon(ecliptic_frame)

    # Elongation of the Moon from the Sun: 0 at new, 180 at full.
    phase = (np.asarray(moon_lon.degrees) - np.asarray(sun_lon.degrees)) % 360.0
    # Illuminated fraction follows from the phase angle directly.
    illuminated = (1.0 - np.cos(np.radians(phase))) / 2.0

    km = np.asarray(moon_dist.km)
    percentile = np.clip(
        (km - LUNAR_PERIGEE_KM) / (LUNAR_APOGEE_KM - LUNAR_PERIGEE_KM), 0.0, 1.0
    )

    # Mean ascending node, retrograde with an 18.6-year period. The mean node is
    # used rather than the true node: the true node oscillates about it by more
    # than a degree with a fortnightly wobble that is noise for this purpose.
    julian = np.asarray(t.tt) - 2451545.0
    centuries = julian / 36525.0
    node = (125.0445479 - 1934.1362891 * centuries) % 360.0

    return LunarState(
        dates=np.asarray(dates, dtype="datetime64[D]"),
        phase_angle=phase,
        illuminated=illuminated,
        distance_km=km,
        distance_percentile=percentile,
        node_longitude=node,
    )


def is_retrograde(body: str, dates) -> np.ndarray:
    """True where the body's apparent longitude is decreasing.

    Measured with a symmetric one-day difference around each date, so the flag
    is centred rather than lagged. The unwrapping matters: at the 360-to-0
    boundary a naive difference reads as a huge negative step and would report
    every planet retrograde once per orbit.
    """
    days = np.asarray(dates, dtype="datetime64[D]")
    before = ecliptic_longitude(body, days - np.timedelta64(1, "D"))
    after = ecliptic_longitude(body, days + np.timedelta64(1, "D"))
    delta = (after - before + 180.0) % 360.0 - 180.0
    return delta < 0.0


def aspect_angle(body_a: str, body_b: str, dates) -> np.ndarray:
    """Angular separation in ecliptic longitude, 0-180 degrees."""
    a = ecliptic_longitude(body_a, dates)
    b = ecliptic_longitude(body_b, dates)
    separation = np.abs((a - b + 180.0) % 360.0 - 180.0)
    return separation


def classify_aspect(
    separation: np.ndarray, orb: float = DEFAULT_ORB
) -> np.ndarray:
    """Name the aspect within ``orb`` degrees, or 'none'."""
    out = np.full(separation.shape, "none", dtype=object)
    for name, exact in ASPECTS.items():
        out[np.abs(separation - exact) <= orb] = name
    return out.astype(str)
