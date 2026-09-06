"""Forward and discount rate extraction by put-call parity regression.

We do not plug in a Treasury yield and a dividend estimate. Both are backed out
of the chain itself, per expiry, from the parity relation:

    C(K) - P(K) = e^{-rT} F - e^{-rT} K

which is linear in K with

    slope     beta  = -e^{-rT}
    intercept alpha =  e^{-rT} F

so that

    r = -ln(-beta) / T
    F = -alpha / beta

This implicitly captures dividends *and* borrow cost, which is exactly why it
beats plugging in a rate: the market's own forward already contains both, and a
hard-coded dividend estimate is the usual source of a skewed surface.

The regression R-squared is recorded per expiry and the expiry is rejected below
``MIN_R2``. A poor parity fit means bad quotes, not an exotic forward.

A precision note that matters when reading the output. The forward is recovered
essentially exactly at every maturity, but the *rate* is not: since

    r = -ln(-beta) / T

any error in the slope is amplified by 1/T. At one week that factor is above 50,
so tick rounding alone moves the extracted rate by tens of basis points, while at
one year the same rounding moves it by less than one. The forward is the number
this module exists to produce and it is reliable throughout; the short-dated rate
is a by-product and should be read as such. Everything downstream uses the
forward, and uses the rate only as a discount factor, where the sensitivity is
second order.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Reject an expiry whose parity regression falls below this. A clean chain fits
# essentially perfectly; anything looser is quote noise.
MIN_R2 = 0.99

# Strikes used for the fit, as a fraction of the forward. Outside this band one
# leg is deep OTM, its quote is a penny-wide fiction, and the difference C - P
# stops being informative about the forward.
MONEYNESS_BAND = (0.85, 1.15)

# The band needs a forward to be defined, and the forward needs the band. We fit
# twice: once on a spot-referenced band to get F0, then again on a band
# referenced to F0. Documented on /methodology as a two-pass fit.
MIN_STRIKES = 4


@dataclass(frozen=True)
class ForwardFit:
    """Result of one expiry's parity regression."""

    forward: float
    rate: float
    r2: float
    n_strikes: int
    accepted: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "forward": self.forward,
            "rate": self.rate,
            "parity_r2": self.r2,
            "n_parity_strikes": self.n_strikes,
            "forward_accepted": self.accepted,
            "forward_reject_reason": self.reason,
        }


def _rejected(reason: str, n: int = 0) -> ForwardFit:
    return ForwardFit(np.nan, np.nan, np.nan, n, False, reason)


def _regress(strikes: np.ndarray, parity: np.ndarray, T: float) -> tuple:
    """Least-squares fit of parity = alpha + beta * K. Returns (F, r, r2)."""
    design = np.column_stack([np.ones_like(strikes), strikes])
    coeffs, *_ = np.linalg.lstsq(design, parity, rcond=None)
    alpha, beta = float(coeffs[0]), float(coeffs[1])

    residuals = parity - design @ coeffs
    ss_res = float(np.sum(np.square(residuals)))
    ss_tot = float(np.sum(np.square(parity - parity.mean())))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # beta must be negative: the discount factor is positive, and the slope is
    # its negative. A non-negative slope is a broken chain, not a real market.
    if beta >= 0.0:
        return np.nan, np.nan, r2

    rate = -np.log(-beta) / T
    forward = -alpha / beta
    return forward, rate, r2


def extract_forward(
    strikes,
    call_mid,
    put_mid,
    T: float,
    *,
    spot: float | None = None,
    band: tuple[float, float] = MONEYNESS_BAND,
    min_r2: float = MIN_R2,
) -> ForwardFit:
    """Extract (F, r) for a single expiry from two-sided call and put mids.

    Only strikes with a finite mid on *both* legs are used — parity is a
    statement about a call and a put at the same strike, so a one-sided strike
    contributes nothing and quietly biases the fit if included.
    """
    strikes = np.asarray(strikes, dtype=float)
    call_mid = np.asarray(call_mid, dtype=float)
    put_mid = np.asarray(put_mid, dtype=float)

    if T <= 0.0:
        return _rejected("expiry_not_in_future")

    both_sided = np.isfinite(call_mid) & np.isfinite(put_mid) & np.isfinite(strikes)
    if both_sided.sum() < MIN_STRIKES:
        return _rejected("too_few_two_sided_strikes", int(both_sided.sum()))

    k_all = strikes[both_sided]
    parity_all = call_mid[both_sided] - put_mid[both_sided]

    # Pass 1: band referenced to spot when we have it, otherwise the whole strip.
    if spot is not None and np.isfinite(spot):
        first = (k_all >= band[0] * spot) & (k_all <= band[1] * spot)
    else:
        first = np.ones_like(k_all, dtype=bool)
    if first.sum() < MIN_STRIKES:
        first = np.ones_like(k_all, dtype=bool)

    f0, _, _ = _regress(k_all[first], parity_all[first], T)
    if not np.isfinite(f0) or f0 <= 0.0:
        return _rejected("pass1_no_valid_forward", int(first.sum()))

    # Pass 2: re-band on the forward itself and refit.
    second = (k_all >= band[0] * f0) & (k_all <= band[1] * f0)
    if second.sum() < MIN_STRIKES:
        return _rejected("too_few_strikes_in_band", int(second.sum()))

    forward, rate, r2 = _regress(k_all[second], parity_all[second], T)
    n = int(second.sum())

    if not np.isfinite(forward) or forward <= 0.0:
        return _rejected("pass2_no_valid_forward", n)
    if r2 < min_r2:
        return ForwardFit(forward, rate, r2, n, False, f"parity_r2_below_{min_r2}")

    return ForwardFit(forward, rate, r2, n, True)
