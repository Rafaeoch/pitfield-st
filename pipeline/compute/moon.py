"""Lunar geometry for rendering: libration, and the actual Sun/Earth directions.

The moon page is rendered from real geometry rather than from a phase parameter
fed into a shader. That distinction is the whole reason the page is worth
building: anyone can draw a crescent, and a drawn crescent is wrong in ways that
are visible to anyone who has looked at the sky.

What "real geometry" means concretely. Instead of computing a terminator, we
export two unit vectors — the direction from the Moon to the Sun, and from the
Moon to the Earth — and let the renderer put its light along the first and its
camera along the second. The terminator is then whatever the lighting model
produces, which is correct by construction, including the ashen-edge cases near
new moon that a hand-drawn crescent always gets wrong.

Libration is the other half. The Moon is tidally locked but rocks visibly: about
+/-7.9 degrees in longitude and +/-6.7 degrees in latitude, which is why we see
roughly 59 percent of its surface over time rather than 50. Ignoring it produces
a moon that is subtly, permanently wrong, and the near-limb craters sit in the
wrong place all month.

Optical libration follows Meeus, *Astronomical Algorithms*, chapter 53. Physical
libration is a further ~0.02 degrees and is neglected; that is below what the
render can show.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..ingest.celestial import _ephemeris, _to_time

# Inclination of the lunar equator to the ecliptic (Meeus 53.1).
LUNAR_EQUATOR_INCLINATION = np.radians(1.54242)


def _julian_centuries(t) -> np.ndarray:
    """Julian centuries from J2000.0, in TT."""
    return (np.asarray(t.tt) - 2451545.0) / 36525.0


def mean_argument_of_latitude(centuries: np.ndarray) -> np.ndarray:
    """F, the Moon's mean argument of latitude, in degrees (Meeus 47.5)."""
    return (
        93.2720950
        + 483202.0175233 * centuries
        - 0.0036539 * centuries**2
        - centuries**3 / 3_526_000.0
        + centuries**4 / 863_310_000.0
    ) % 360.0


def mean_ascending_node(centuries: np.ndarray) -> np.ndarray:
    """Omega, longitude of the mean ascending node, in degrees (Meeus 47.7).

    Retrograde: it completes a circuit in 18.6 years, which is the cycle the
    `lunar_nodes` hypothesis is built on.
    """
    return (
        125.0445479
        - 1934.1362891 * centuries
        + 0.0020754 * centuries**2
        + centuries**3 / 467_441.0
        - centuries**4 / 60_616_000.0
    ) % 360.0


@dataclass(frozen=True)
class MoonGeometry:
    """Everything the renderer needs, plus what the page displays."""

    libration_longitude: np.ndarray   # degrees, +/- ~7.9
    libration_latitude: np.ndarray    # degrees, +/- ~6.7
    sun_direction: np.ndarray         # (N, 3) unit vectors, ecliptic frame
    earth_direction: np.ndarray       # (N, 3) unit vectors, ecliptic frame
    phase_angle: np.ndarray           # Sun-Moon-Earth angle; 0 = full, 180 = new
    elongation: np.ndarray            # Moon - Sun ecliptic longitude; 0 = new
    illuminated: np.ndarray           # 0-1
    distance_km: np.ndarray


def _unit(vectors: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.where(norm == 0, 1.0, norm)


def optical_libration(dates) -> tuple[np.ndarray, np.ndarray]:
    """Optical libration in longitude and latitude, degrees (Meeus 53).

    Derived from the Moon's apparent ecliptic position and the orientation of
    its equator, not from a lookup table.
    """
    from skyfield.framelib import ecliptic_frame

    eph = _ephemeris()
    t = _to_time(dates)
    lat, lon, _ = (
        eph["earth"].at(t).observe(eph["moon"]).apparent().frame_latlon(ecliptic_frame)
    )

    centuries = _julian_centuries(t)
    node = np.radians(mean_ascending_node(centuries))
    arg_lat = mean_argument_of_latitude(centuries)

    lam = np.radians(np.asarray(lon.degrees))
    beta = np.radians(np.asarray(lat.degrees))
    inc = LUNAR_EQUATOR_INCLINATION

    # W is the Moon's longitude measured from the ascending node of its equator.
    w = lam - node

    a = np.arctan2(
        np.sin(w) * np.cos(beta) * np.cos(inc) - np.sin(beta) * np.sin(inc),
        np.cos(w) * np.cos(beta),
    )

    lib_lon = (np.degrees(a) - arg_lat + 180.0) % 360.0 - 180.0
    lib_lat = np.degrees(
        np.arcsin(
            -np.sin(w) * np.cos(beta) * np.sin(inc) - np.sin(beta) * np.cos(inc)
        )
    )
    return lib_lon, lib_lat


def moon_geometry(dates) -> MoonGeometry:
    """Full render geometry for each date."""
    from skyfield.framelib import ecliptic_frame

    eph = _ephemeris()
    t = _to_time(dates)

    moon_at = eph["moon"].at(t)
    # Directions measured *from the Moon*, which is what the renderer needs.
    to_sun = moon_at.observe(eph["sun"]).apparent()
    to_earth = moon_at.observe(eph["earth"]).apparent()

    sun_xyz = np.atleast_2d(np.asarray(to_sun.frame_xyz(ecliptic_frame).km).T)
    earth_xyz = np.atleast_2d(np.asarray(to_earth.frame_xyz(ecliptic_frame).km).T)

    sun_dir = _unit(sun_xyz)
    earth_dir = _unit(earth_xyz)

    # Sun-Moon-Earth angle. 0 means Earth and Sun are the same side: full moon.
    cos_phase = np.clip(np.sum(sun_dir * earth_dir, axis=-1), -1.0, 1.0)
    phase_angle = np.degrees(np.arccos(cos_phase))
    illuminated = (1.0 + cos_phase) / 2.0

    _, moon_lon, moon_dist = (
        eph["earth"].at(t).observe(eph["moon"]).apparent().frame_latlon(ecliptic_frame)
    )
    _, sun_lon, _ = (
        eph["earth"].at(t).observe(eph["sun"]).apparent().frame_latlon(ecliptic_frame)
    )
    elongation = (np.asarray(moon_lon.degrees) - np.asarray(sun_lon.degrees)) % 360.0

    lib_lon, lib_lat = optical_libration(dates)

    return MoonGeometry(
        libration_longitude=np.atleast_1d(lib_lon),
        libration_latitude=np.atleast_1d(lib_lat),
        sun_direction=sun_dir,
        earth_direction=earth_dir,
        phase_angle=np.atleast_1d(phase_angle),
        elongation=np.atleast_1d(elongation),
        illuminated=np.atleast_1d(illuminated),
        distance_km=np.atleast_1d(np.asarray(moon_dist.km)),
    )
