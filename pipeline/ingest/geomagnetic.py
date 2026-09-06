"""Planetary K-index from GFZ Potsdam, daily, back to 1932.

Kp measures geomagnetic disturbance on a quasi-logarithmic 0-9 scale, reported
every three hours. GFZ Potsdam is the issuing authority and serves the full
record without a key; NOAA SWPC serves only a recent window, so it is useful for
the live tail and useless for a study that wants ninety years.

Krivelyova & Robotti (Atlanta Fed WP 2003-5) is the specification being
replicated, and it defines a storm on the *daily* series. Kp arrives 3-hourly,
so the aggregation is a choice with consequences and is made explicitly here:

* ``kp_max``  — the largest of the day's eight readings. This is the storm
  measure. A six-hour severe disturbance is a storm even if the rest of the day
  was quiet, and a daily mean would dilute exactly that signal.
* ``kp_mean`` — the day's average, retained as a secondary measure.

Neither is imputed. A day with missing readings reports what it has and records
how many readings it had, because a "quiet day" built from two observations is
not the same object as one built from eight.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sources import fetch

GFZ_URL = "https://kp.gfz-potsdam.de/kpdata"

# GFZ writes -1 for a reading that does not exist.
MISSING = -0.5

# Krivelyova & Robotti's threshold. A storm day is Kp >= 7 at any point.
STORM_THRESHOLD = 7.0


@dataclass(frozen=True)
class KpSeries:
    dates: np.ndarray      # datetime64[D]
    kp_max: np.ndarray
    kp_mean: np.ndarray
    n_readings: np.ndarray
    source_url: str
    fetched_at: str
    sha256: str

    def __len__(self) -> int:
        return int(self.dates.size)

    @property
    def is_storm(self) -> np.ndarray:
        return self.kp_max >= STORM_THRESHOLD


def load_kp(
    start: str = "1932-01-01", end: str = "2026-12-31", *, refresh: bool = False
) -> KpSeries:
    """Download (or read from cache) the 3-hourly Kp record and aggregate to days."""
    payload = fetch(
        GFZ_URL,
        params={"startdate": start, "enddate": end, "format": "kp2"},
        refresh=refresh,
        timeout=180,
    )

    by_day: dict[np.datetime64, list[float]] = {}
    for line in payload.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        # YYYY MM DD hh.h hh._m days days_m Kp ap D
        if len(parts) < 8:
            continue
        try:
            day = np.datetime64(f"{parts[0]}-{parts[1]}-{parts[2]}", "D")
            kp = float(parts[7])
        except (ValueError, IndexError):
            continue
        if kp < MISSING:
            continue
        by_day.setdefault(day, []).append(kp)

    if not by_day:
        raise ValueError("no Kp readings parsed; the GFZ format may have changed")

    days = np.array(sorted(by_day))
    return KpSeries(
        dates=days,
        kp_max=np.array([max(by_day[d]) for d in days]),
        kp_mean=np.array([float(np.mean(by_day[d])) for d in days]),
        n_readings=np.array([len(by_day[d]) for d in days]),
        source_url=payload.url,
        fetched_at=payload.fetched_at,
        sha256=payload.sha256,
    )
