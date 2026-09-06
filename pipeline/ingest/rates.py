"""The US Treasury constant-maturity curve, from FRED. Free, no key.

Why this module exists.

The pipeline's preferred discount rate comes out of put-call parity, which is
the right way to do it: the rate implied by the chain contains the market's own
financing and borrow, not an approximation of it. But that extraction needs
quotes that satisfy parity, and a derived feed does not — on Alpaca's
indicative feed every expiry returns a negative rate.

The previous fallback was a flat assumed 4.35%. This is strictly better and
costs nothing: a real, dated, published curve, interpolated to each expiry.

Quoting convention matters and is handled rather than ignored. Treasury CMT
yields are par yields quoted on a semi-annual bond-equivalent basis. Black-76
discounts continuously. Converting:

    r_continuous = 2 * ln(1 + y_semiannual / 2)

At 4% over a year that is a 2 basis point difference, which is small — and
small is not the same as absent, so it is done.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from .sources import fetch

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# FRED series id -> maturity in years.
CURVE = {
    "DGS1MO": 1 / 12,
    "DGS3MO": 0.25,
    "DGS6MO": 0.5,
    "DGS1": 1.0,
    "DGS2": 2.0,
    "DGS3": 3.0,
}


@dataclass(frozen=True)
class TreasuryCurve:
    as_of: date
    maturities: np.ndarray      # years
    rates: np.ndarray           # continuously compounded
    source: str = "FRED constant-maturity Treasury"

    def rate_for(self, T: float) -> float:
        """Continuously compounded rate at maturity ``T`` years.

        Linear in maturity between the published points, flat beyond the ends.
        Flat rather than extrapolated: a curve told to guess past three years
        will happily produce a negative rate at ten, and this module exists to
        stop exactly that class of nonsense.
        """
        if not np.isfinite(T) or T <= 0 or self.maturities.size == 0:
            return float("nan")
        return float(np.interp(T, self.maturities, self.rates))

    def as_dict(self) -> dict:
        return {
            "as_of": str(self.as_of),
            "source": self.source,
            "points": [
                {"maturity_years": round(float(m), 4), "rate": round(float(r), 6)}
                for m, r in zip(self.maturities, self.rates)
            ],
        }


def _latest_observation(series_id: str, on: date | None, refresh: bool) -> tuple[date, float] | None:
    payload = fetch(FRED_CSV, params={"id": series_id}, refresh=refresh)
    best = None
    for line in payload.text.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        raw_date, raw_value = parts[0].strip(), parts[1].strip()
        if raw_value in {".", ""}:          # a holiday, not a zero rate
            continue
        try:
            observed = date.fromisoformat(raw_date)
            value = float(raw_value)
        except ValueError:
            continue
        if on is not None and observed > on:
            break
        best = (observed, value)
    return best


def load_curve(on: date | None = None, *, refresh: bool = False) -> TreasuryCurve:
    """The curve as of ``on`` (default: latest available).

    Uses the last observation on or before the date, which is the correct
    point-in-time behaviour: on a Monday the relevant curve is Friday's, and
    reaching forward for a later print would be lookahead.
    """
    maturities, rates, as_of = [], [], None
    for series_id, maturity in sorted(CURVE.items(), key=lambda kv: kv[1]):
        got = _latest_observation(series_id, on, refresh)
        if got is None:
            continue
        observed, percent = got
        # Percent, semi-annual bond-equivalent -> continuous.
        annual = percent / 100.0
        continuous = 2.0 * np.log1p(annual / 2.0)
        maturities.append(maturity)
        rates.append(continuous)
        as_of = observed if as_of is None else max(as_of, observed)

    if not maturities:
        raise ValueError("no Treasury observations available from FRED")

    return TreasuryCurve(
        as_of=as_of,
        maturities=np.array(maturities, dtype=float),
        rates=np.array(rates, dtype=float),
    )


def implied_dividend_yield(forward: float, spot: float, T: float, rate: float) -> float:
    """Back the carry out of the forward, given a rate.

        F = S e^{(r - q)T}   =>   q = r - ln(F/S) / T

    This is worth publishing. The forward is the one quantity that survives on
    a derived feed — it is a ratio of the parity regression's intercept to its
    slope, so a scale error in the slope largely cancels. Combine a reliable
    forward with a real Treasury rate and what falls out is the market's implied
    dividend-plus-borrow, which is a number almost nobody publishes and which
    this pipeline gets for free.
    """
    if not all(np.isfinite([forward, spot, T, rate])) or T <= 0 or spot <= 0 or forward <= 0:
        return float("nan")
    return float(rate - np.log(forward / spot) / T)
