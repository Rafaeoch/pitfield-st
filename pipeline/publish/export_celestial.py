"""Export real ephemeris data for the celestial pages.

Everything the moon page and the orrery render comes from this file, which comes
from DE440s. Nothing on those pages is decorative astronomy: the terminator, the
libration, the planetary positions and the aspect geometry are all computed, and
they would be visibly wrong if they were not.

The orrery exports longitudes only. Aspect classification is done in the browser
from those longitudes, because the aspect set and orb are a display choice the
reader can change, and shipping 28 pairs x N dates of precomputed labels to
support that would be larger and less flexible than shipping the positions.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from ..compute.moon import moon_geometry
from ..ingest.celestial import BODIES, ecliptic_longitude, is_retrograde, lunar_state

# Bodies drawn on the orrery wheel, in orbital order outward.
ORRERY_BODIES = [
    "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto",
]

# Zodiacal signs: 12 equal 30-degree divisions of the ecliptic starting at the
# vernal equinox. This is the tropical zodiac, which is what mundane astrology
# uses. It has drifted about 24 degrees from the constellations that share its
# names, owing to precession — a fact the page states rather than hides.
SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]


def _round(a, dp=4):
    return [round(float(v), dp) for v in np.asarray(a).ravel()]


def find_phase_events(start: date, days: int = 60) -> dict:
    """Timestamps of the next new and full moon, found by search not by table.

    Sampled hourly and refined: the elongation crosses 0 at new and 180 at full,
    so we look for the crossing rather than adding 29.53 days to the last one,
    which accumulates error.
    """
    hours = np.arange(days * 24)
    stamps = np.datetime64(start, "h") + hours.astype("timedelta64[h]")
    as_days = stamps.astype("datetime64[D]")

    # Sample elongation hourly by evaluating at each hour's date, then refine
    # using the underlying continuous geometry.
    from ..ingest.celestial import _ephemeris, _timescale
    from skyfield.framelib import ecliptic_frame

    eph = _ephemeris()
    ts = _timescale()
    years = stamps.astype("datetime64[Y]").astype(int) + 1970
    months = stamps.astype("datetime64[M]").astype(int) % 12 + 1
    dom = (stamps.astype("datetime64[D]") - stamps.astype("datetime64[M]")).astype(int) + 1
    hour = (stamps - stamps.astype("datetime64[D]")).astype("timedelta64[h]").astype(int)
    t = ts.utc(years, months, dom, hour)

    earth = eph["earth"].at(t)
    _, moon_lon, _ = earth.observe(eph["moon"]).apparent().frame_latlon(ecliptic_frame)
    _, sun_lon, _ = earth.observe(eph["sun"]).apparent().frame_latlon(ecliptic_frame)
    elong = (np.asarray(moon_lon.degrees) - np.asarray(sun_lon.degrees)) % 360.0

    def first_crossing(target: float):
        shifted = (elong - target + 180.0) % 360.0 - 180.0
        sign_change = np.where(np.diff(np.sign(shifted)) > 0)[0]
        if sign_change.size == 0:
            return None
        i = int(sign_change[0])
        return str(stamps[i + 1])

    return {
        "next_new_moon": first_crossing(0.0),
        "next_full_moon": first_crossing(180.0),
    }


def build(asof: date, orrery_years: float = 2.0) -> dict:
    """The full celestial payload."""
    today = np.datetime64(asof, "D")

    geom = moon_geometry([today])
    state = lunar_state([today])

    lon = float(ecliptic_longitude("mercury", [today])[0])  # warm the kernel

    # --- orrery: daily longitudes over a scrubbing window -----------------
    span = int(orrery_years * 365)
    delta = np.timedelta64(span, "D")
    window = np.arange(today - delta, today + delta + np.timedelta64(1, "D"), dtype="datetime64[D]")

    tracks = {}
    for body in ORRERY_BODIES:
        tracks[body] = {
            "longitude": _round(ecliptic_longitude(body, window), 3),
            "retrograde": [bool(v) for v in is_retrograde(body, window)],
        }
    moon_window = lunar_state(window)
    tracks["moon"] = {
        "longitude": _round(
            ecliptic_longitude("moon", window) if "moon" in BODIES else
            _moon_longitude(window), 3
        ),
        "retrograde": [False] * window.size,
    }
    tracks["sun"] = {
        "longitude": _round(_sun_longitude(window), 3),
        "retrograde": [False] * window.size,
    }

    # --- moon history: geometry for every day in the same window ----------
    # The moon page scrubs through these, re-lighting and re-rotating the
    # render for each date. Exported rather than computed in the browser
    # because it is an ephemeris lookup, not arithmetic: approximating it
    # client-side is exactly the shortcut this project refuses to take.
    hist = moon_geometry(window)
    hist_state = lunar_state(window)
    moon_history = {
        "start": str(window[0]),
        "n_days": int(window.size),
        "today_index": int(np.where(window == today)[0][0]),
        "illuminated": _round(hist.illuminated, 4),
        "phase_angle": _round(hist.phase_angle, 2),
        "distance_km": [int(round(float(v))) for v in hist.distance_km],
        "libration_longitude": _round(hist.libration_longitude, 3),
        "libration_latitude": _round(hist.libration_latitude, 3),
        "phase_name": [str(v) for v in hist_state.phase_bucket()],
        # Flattened triplets keep the payload compact; the client reads them
        # three at a time.
        "sun_direction": _round(hist.sun_direction.reshape(-1), 5),
        "earth_direction": _round(hist.earth_direction.reshape(-1), 5),
    }

    return {
        "date": str(asof),
        "generated_from": "JPL DE440s via Skyfield",
        "moon_history": moon_history,
        "moon": {
            "phase_angle": round(float(geom.phase_angle[0]), 4),
            "elongation": round(float(geom.elongation[0]), 4),
            "illuminated": round(float(geom.illuminated[0]), 6),
            "distance_km": round(float(geom.distance_km[0]), 1),
            "distance_percentile": round(float(state.distance_percentile[0]), 4),
            "libration_longitude": round(float(geom.libration_longitude[0]), 4),
            "libration_latitude": round(float(geom.libration_latitude[0]), 4),
            "sun_direction": _round(geom.sun_direction[0], 6),
            "earth_direction": _round(geom.earth_direction[0], 6),
            "phase_name": str(state.phase_bucket()[0]),
            "node_longitude": round(float(state.node_longitude[0]), 4),
            **find_phase_events(asof),
        },
        "orrery": {
            "start": str(window[0]),
            "end": str(window[-1]),
            "n_days": int(window.size),
            "today_index": int(np.where(window == today)[0][0]),
            "signs": SIGNS,
            "bodies": tracks,
        },
    }


def _moon_longitude(dates) -> np.ndarray:
    from ..ingest.celestial import _ephemeris, _to_time
    from skyfield.framelib import ecliptic_frame

    eph = _ephemeris()
    _, lon, _ = (
        eph["earth"].at(_to_time(dates)).observe(eph["moon"]).apparent()
        .frame_latlon(ecliptic_frame)
    )
    return np.asarray(lon.degrees) % 360.0


def _sun_longitude(dates) -> np.ndarray:
    from ..ingest.celestial import _ephemeris, _to_time
    from skyfield.framelib import ecliptic_frame

    eph = _ephemeris()
    _, lon, _ = (
        eph["earth"].at(_to_time(dates)).observe(eph["sun"]).apparent()
        .frame_latlon(ecliptic_frame)
    )
    return np.asarray(lon.degrees) % 360.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("site/public/data"))
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--orrery-years", type=float, default=2.0)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    payload = build(args.date, args.orrery_years)

    # Split by page. The moon page has no use for planetary tracks and the
    # study page has no use for lunar libration; shipping both to each doubles
    # the payload for nothing.
    moon_payload = {
        "date": payload["date"],
        "generated_from": payload["generated_from"],
        "moon": payload["moon"],
        "moon_history": payload["moon_history"],
    }
    orrery_payload = {
        "date": payload["date"],
        "generated_from": payload["generated_from"],
        "orrery": payload["orrery"],
    }

    path = args.out / "moon.json"
    path.write_text(json.dumps(moon_payload, separators=(",", ":")))
    orrery_path = args.out / "orrery.json"
    orrery_path.write_text(json.dumps(orrery_payload, separators=(",", ":")))

    moon = payload["moon"]
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    print(f"wrote {orrery_path} ({orrery_path.stat().st_size / 1024:.0f} KB)")
    print(f"  moon: {moon['phase_name']}, {moon['illuminated'] * 100:.1f}% lit, "
          f"{moon['distance_km']:,.0f} km")
    print(f"  libration: lon {moon['libration_longitude']:+.2f}  "
          f"lat {moon['libration_latitude']:+.2f}")
    print(f"  next new : {moon['next_new_moon']}")
    print(f"  next full: {moon['next_full_moon']}")
    print(f"  orrery: {payload['orrery']['n_days']} days, "
          f"{len(payload['orrery']['bodies'])} bodies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
