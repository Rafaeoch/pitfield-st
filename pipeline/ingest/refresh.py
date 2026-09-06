"""Pull every free public input and report what came back.

Run with ``make inputs``. Everything here is free, unauthenticated and cached,
so re-running is cheap and does not pester the sources.
"""

from __future__ import annotations

import sys

from .celestial import KERNEL_PATH, lunar_state
from .geomagnetic import load_kp
from .market_history import SERIES, load_series
from .solar import load_sunspots
from .sources import provenance

import numpy as np


def main() -> int:
    print("Pulling free public inputs\n")

    for series_id in SERIES:
        try:
            s = load_series(series_id)
            print(f"  {series_id:<12} {len(s):>7,} days  {s.dates[0]} -> {s.dates[-1]}")
        except Exception as exc:
            print(f"  {series_id:<12} FAILED: {type(exc).__name__}: {exc}")

    try:
        kp = load_kp()
        print(f"  {'Kp (GFZ)':<12} {len(kp):>7,} days  {kp.dates[0]} -> {kp.dates[-1]}"
              f"   storms: {int(kp.is_storm.sum())}")
    except Exception as exc:
        print(f"  {'Kp (GFZ)':<12} FAILED: {type(exc).__name__}: {exc}")

    try:
        ss = load_sunspots()
        print(f"  {'Sunspots':<12} {len(ss):>7,} days  {ss.dates[0]} -> {ss.dates[-1]}")
    except Exception as exc:
        print(f"  {'Sunspots':<12} FAILED: {type(exc).__name__}: {exc}")

    if KERNEL_PATH.exists():
        days = np.arange(np.datetime64("2026-01-01"), np.datetime64("2026-01-08"),
                         dtype="datetime64[D]")
        moon = lunar_state(days)
        print(f"  {'Ephemeris':<12} DE440s loaded; moon illumination sample "
              f"{moon.illuminated[0]:.3f} -> {moon.illuminated[-1]:.3f}")
    else:
        print(f"  {'Ephemeris':<12} MISSING - run: make ephemeris")

    print("\nProvenance (cached payloads):")
    for record in provenance():
        print(f"  {record['sha256'][:12]}  {record['bytes']:>10,}B  {record['url'][:78]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
