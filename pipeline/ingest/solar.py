"""Daily total sunspot number from SILSO, Royal Observatory of Belgium.

SILSO is the world reference for the sunspot number and publishes the daily
series back to 1818 without a key. The Schwabe cycle it traces is roughly eleven
years, which is why the study needs a return series measured in decades rather
than years to say anything about it at all.

Missing days are marked -1 in the source and are dropped, not carried forward.
Before about 1850 there are long gaps where nobody was observing; a
forward-filled sunspot number across a gap is fiction, and it is fiction that
would look like a persistent regime to any bucketing rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sources import fetch

SILSO_URL = "https://www.sidc.be/SILSO/INFO/sndtotcsv.php"


@dataclass(frozen=True)
class SunspotSeries:
    dates: np.ndarray          # datetime64[D]
    number: np.ndarray         # daily total sunspot number
    definitive: np.ndarray     # bool; provisional values may be revised
    source_url: str
    fetched_at: str
    sha256: str

    def __len__(self) -> int:
        return int(self.dates.size)


def load_sunspots(*, refresh: bool = False) -> SunspotSeries:
    """Download (or read from cache) the SILSO daily total sunspot number."""
    payload = fetch(SILSO_URL, refresh=refresh, timeout=180)

    dates, numbers, definitive = [], [], []
    for line in payload.text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        # year;month;day;decimal_year;SN;SN_error;n_obs;definitive
        if len(parts) < 8:
            continue
        try:
            value = float(parts[4])
            if value < 0:          # -1 marks a day with no observation
                continue
            day = np.datetime64(f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}", "D")
        except (ValueError, IndexError):
            continue
        dates.append(day)
        numbers.append(value)
        definitive.append(parts[7] == "1")

    if not dates:
        raise ValueError("no sunspot rows parsed; the SILSO format may have changed")

    order = np.argsort(np.array(dates))
    return SunspotSeries(
        dates=np.array(dates)[order],
        number=np.array(numbers, dtype=float)[order],
        definitive=np.array(definitive, dtype=bool)[order],
        source_url=payload.url,
        fetched_at=payload.fetched_at,
        sha256=payload.sha256,
    )
