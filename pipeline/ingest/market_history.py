"""Daily equity index history from FRED.

FRED is the Federal Reserve Bank of St Louis. It serves these series free, with
no API key and no scraping, which is why it is the source here: the celestial
study has to be reproducible by anyone who clones this repository, and a study
whose return series sits behind a paid key is not.

A note on which index, because it is a deviation from the pre-registration and
is recorded as one.

FRED carries the S&P 500 under licence and is only permitted to publish the
trailing ten years of it — about 2,600 observations, which is far too short to
say anything about an 18.6-year lunar node cycle or an 11-year solar cycle. The
NASDAQ Composite carries no such restriction and runs from February 1971:
roughly 14,500 daily observations, and long enough for the cycles in the
hypothesis list to complete several times.

So the primary series is the NASDAQ Composite and the S&P 500 is retained as a
secondary robustness check over its shorter window. The NASDAQ Composite is more
concentrated in technology than a broad-market index, and that is a genuine
limitation rather than a detail — it is stated on the results page, not buried
here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sources import fetch

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

SERIES = {
    # id -> (label, note)
    "NASDAQCOM": ("NASDAQ Composite", "primary; daily from 1971-02-05"),
    "SP500": ("S&P 500", "secondary; FRED may publish only a 10-year window"),
}

PRIMARY_SERIES = "NASDAQCOM"


@dataclass(frozen=True)
class PriceSeries:
    series_id: str
    label: str
    dates: np.ndarray      # datetime64[D]
    close: np.ndarray      # float
    source_url: str
    fetched_at: str
    sha256: str

    def __len__(self) -> int:
        return int(self.dates.size)

    @property
    def log_returns(self) -> np.ndarray:
        """Close-to-close log returns, aligned to ``dates[1:]``."""
        return np.diff(np.log(self.close))

    @property
    def return_dates(self) -> np.ndarray:
        return self.dates[1:]

    def forward_return(self, horizon: int) -> tuple[np.ndarray, np.ndarray]:
        """Log return over the next ``horizon`` trading days.

        Returned aligned to the date the window *starts* from, and truncated so
        that no observation is produced for a window extending past the end of
        the data. Padding the tail would be a lookahead bug.
        """
        if horizon < 1 or self.close.size <= horizon:
            return np.array([], dtype="datetime64[D]"), np.array([])
        forward = np.log(self.close[horizon:] / self.close[:-horizon])
        return self.dates[:-horizon], forward


def load_series(series_id: str = PRIMARY_SERIES, *, refresh: bool = False) -> PriceSeries:
    """Download (or read from cache) one FRED daily series."""
    if series_id not in SERIES:
        raise ValueError(f"unknown series {series_id!r}; expected one of {sorted(SERIES)}")

    payload = fetch(FRED_CSV, params={"id": series_id}, refresh=refresh)
    lines = payload.text.strip().splitlines()
    if not lines or "observation_date" not in lines[0]:
        raise ValueError(f"unexpected FRED payload for {series_id}: {lines[:1]}")

    dates, closes = [], []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        raw_date, raw_value = parts[0].strip(), parts[1].strip()
        # FRED writes "." for a non-observation (market holidays and the like).
        # These are dropped rather than interpolated: a made-up close would
        # create a return that never happened.
        if raw_value in {".", ""}:
            continue
        try:
            closes.append(float(raw_value))
        except ValueError:
            continue
        dates.append(np.datetime64(raw_date, "D"))

    if not dates:
        raise ValueError(f"no usable observations in FRED series {series_id}")

    order = np.argsort(np.array(dates))
    label, _ = SERIES[series_id]
    return PriceSeries(
        series_id=series_id,
        label=label,
        dates=np.array(dates)[order],
        close=np.array(closes, dtype=float)[order],
        source_url=payload.url,
        fetched_at=payload.fetched_at,
        sha256=payload.sha256,
    )
