"""Daily quality control. Log and flag; never silently drop.

The share of a chain excluded from each fit is itself a published number. A page
that shows what was thrown away is more informative than one that only shows
what survived, and it is the first thing a reader who knows the subject looks
for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

# A quote older than this before the snapshot is stale: the contract has not
# traded or been re-quoted near the close, so its mid is a fossil.
STALE_QUOTE_SECONDS = 900


@dataclass
class QualityReport:
    """One underlying's QC for one day."""

    date: str
    underlying: str
    n_rows: int = 0
    n_crossed: int = 0
    n_locked: int = 0
    n_zero_bid: int = 0
    n_stale: int = 0
    n_adjusted: int = 0
    zero_bid_share: float = 0.0
    crossed_share: float = 0.0
    stale_share: float = 0.0
    strike_gaps: list[dict] = field(default_factory=list)
    worst_crossed: list[dict] = field(default_factory=list)
    expiries_fitted: int = 0
    expiries_rejected: int = 0
    fit_failures: list[dict] = field(default_factory=list)
    row_count_median_20d: float | None = None
    row_count_delta_pct: float | None = None
    row_count_alarm: bool = False
    is_synthetic: bool = False
    passed: bool = True

    def as_dict(self) -> dict:
        out = dict(self.__dict__)
        return out


def _worst_crossed(rows: list[dict], limit: int = 5) -> list[dict]:
    crossed = [
        {
            "expiry": str(r["expiry"]),
            "strike": r["strike"],
            "right": r["right"],
            "bid": r["bid"],
            "ask": r["ask"],
            "cross": r["bid"] - r["ask"],
        }
        for r in rows
        if r["bid"] > r["ask"]
    ]
    crossed.sort(key=lambda d: -d["cross"])
    return crossed[:limit]


def _strike_gaps(rows: list[dict]) -> list[dict]:
    """Strike increments that jump more than 2x the modal increment for an expiry."""
    by_expiry: dict = {}
    for r in rows:
        by_expiry.setdefault(str(r["expiry"]), set()).add(r["strike"])

    gaps = []
    for expiry, strikes in by_expiry.items():
        ks = np.array(sorted(strikes))
        if ks.size < 3:
            continue
        diffs = np.diff(ks)
        modal = float(np.median(diffs))
        if modal <= 0:
            continue
        for i in np.where(diffs > 2.0 * modal)[0]:
            gaps.append(
                {
                    "expiry": expiry,
                    "after_strike": float(ks[i]),
                    "before_strike": float(ks[i + 1]),
                    "gap": float(diffs[i]),
                    "expected_increment": modal,
                }
            )
    return gaps


def run_checks(
    rows: list[dict],
    *,
    date: str,
    underlying: str,
    snapshot_ts: datetime,
    fit_failures: list[dict] | None = None,
    expiries_fitted: int = 0,
    expiries_rejected: int = 0,
    trailing_row_counts: list[int] | None = None,
) -> QualityReport:
    """Run the full QC suite over one underlying's chain."""
    report = QualityReport(date=date, underlying=underlying, n_rows=len(rows))
    if not rows:
        report.passed = False
        return report

    bid = np.array([r["bid"] for r in rows], dtype=float)
    ask = np.array([r["ask"] for r in rows], dtype=float)

    report.n_crossed = int(np.sum(bid > ask))
    report.n_locked = int(np.sum(bid == ask))
    report.n_zero_bid = int(np.sum(bid <= 0.0))
    report.n_adjusted = int(sum(1 for r in rows if r.get("is_adjusted")))
    report.is_synthetic = any(r.get("source") == "SYNTHETIC" for r in rows)

    stale_cutoff = snapshot_ts - timedelta(seconds=STALE_QUOTE_SECONDS)
    report.n_stale = int(
        sum(
            1
            for r in rows
            if r.get("quote_ts") is not None and r["quote_ts"] < stale_cutoff
        )
    )

    n = float(len(rows))
    report.zero_bid_share = report.n_zero_bid / n
    report.crossed_share = report.n_crossed / n
    report.stale_share = report.n_stale / n

    report.worst_crossed = _worst_crossed(rows)
    report.strike_gaps = _strike_gaps(rows)
    report.fit_failures = fit_failures or []
    report.expiries_fitted = expiries_fitted
    report.expiries_rejected = expiries_rejected

    # A sudden drop in row count means a broken source, not a quiet market.
    if trailing_row_counts:
        median = float(np.median(trailing_row_counts))
        report.row_count_median_20d = median
        if median > 0:
            delta = (len(rows) - median) / median
            report.row_count_delta_pct = float(delta * 100.0)
            report.row_count_alarm = bool(delta < -0.30)

    # A day fails QC on structural problems, not on cosmetic ones. Crossed
    # markets above 1% of the chain, or a collapsed row count, mean the source
    # is wrong and the day should not be trusted.
    report.passed = (
        report.crossed_share <= 0.01
        and not report.row_count_alarm
        and report.expiries_fitted > 0
    )
    return report
