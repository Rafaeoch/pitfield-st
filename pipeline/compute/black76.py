"""Black-76 pricing, implied-vol inversion, and greeks on the extracted forward.

Everything here prices off the forward F, never off spot. Dividends and borrow
cost are already inside F (see :mod:`pipeline.compute.forward`), which is why no
dividend yield appears anywhere in this module. That is the entire reason for
working in the forward measure rather than patching a dividend estimate into a
spot-based model.

Conventions, stated once here and pinned by the tests so they cannot drift:

  * **Vega** is reported per volatility *point* — per 0.01 of sigma.
  * **Theta** and **charm** are reported per *calendar* day.
  * Delta, gamma, vanna and volga are per unit of forward, discounted to today.

Half of all greek disagreements are unit disagreements, so the units are part of
the output schema, not a footnote.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

# Finite-difference bump for theta and charm, in years: one calendar day.
# Closed forms exist, but a bump is easier to verify against a repriced option,
# and the test suite checks it against the closed form to 1e-4. Documented on
# /methodology.
THETA_BUMP_YEARS = 1.0 / 365.0

_SQRT_2PI = np.sqrt(2.0 * np.pi)

# Bracket for the Brent implied-vol search. 1e-4 to 500% annualised: anything
# outside this is a bad quote, not a real volatility.
IV_BRACKET = (1e-4, 5.0)


def _phi(x: np.ndarray) -> np.ndarray:
    """Standard normal PDF."""
    return np.exp(-0.5 * np.square(x)) / _SQRT_2PI


def d1_d2(F, K, T, sigma):
    """Return the Black-76 d1 and d2.

        d1 = ( ln(F/K) + sigma^2 T / 2 ) / ( sigma sqrt(T) )
        d2 = d1 - sigma sqrt(T)

    Degenerate inputs (T <= 0 or sigma <= 0) return +/-inf in the correct
    direction so that :func:`price` collapses to the discounted intrinsic value
    rather than producing a NaN.
    """
    F, K, T, sigma = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (F, K, T, sigma))
    )
    vol_sqrt_t = sigma * np.sqrt(np.maximum(T, 0.0))

    with np.errstate(divide="ignore", invalid="ignore"):
        log_fk = np.log(F / K)
        d1 = (log_fk + 0.5 * np.square(sigma) * T) / vol_sqrt_t
        d2 = d1 - vol_sqrt_t

    # At zero variance the option is worth its intrinsic: push d1/d2 to the
    # limit implied by moneyness.
    degenerate = vol_sqrt_t <= 0.0
    if np.any(degenerate):
        limit = np.where(log_fk > 0.0, np.inf, np.where(log_fk < 0.0, -np.inf, 0.0))
        d1 = np.where(degenerate, limit, d1)
        d2 = np.where(degenerate, limit, d2)

    return d1, d2


def price(F, K, T, sigma, r, right):
    """Black-76 price of a European option on the forward.

        C = e^{-rT} [ F N(d1) - K N(d2) ]
        P = e^{-rT} [ K N(-d2) - F N(-d1) ]

    ``right`` is ``"C"`` or ``"P"``, scalar or array.
    """
    d1, d2 = d1_d2(F, K, T, sigma)
    F, K, T, r = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (F, K, T, r))
    )
    disc = np.exp(-r * T)
    is_call = np.asarray(right) == "C"

    call = disc * (F * norm.cdf(d1) - K * norm.cdf(d2))
    put = disc * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
    return np.where(is_call, call, put)


def implied_vol(target_price, F, K, T, r, right):
    """Invert a Black-76 price to a volatility with Brent on a bracketed interval.

    Returns NaN when the price is outside the no-arbitrage bounds or the search
    fails to bracket a root. Callers drop those contracts and log them — they are
    never silently coerced to a number (see the QC suite).
    """
    target_price = np.asarray(target_price, dtype=float)
    scalar = target_price.ndim == 0
    target_price, F, K, T, r, right = (
        np.atleast_1d(np.asarray(v))
        for v in np.broadcast_arrays(target_price, F, K, T, r, np.asarray(right))
    )

    out = np.full(target_price.shape, np.nan, dtype=float)
    lo, hi = IV_BRACKET

    for i in range(target_price.size):
        p, f, k, t, rate, w = (
            float(target_price.flat[i]),
            float(F.flat[i]),
            float(K.flat[i]),
            float(T.flat[i]),
            float(r.flat[i]),
            str(right.flat[i]),
        )
        if not np.isfinite(p) or p <= 0.0 or t <= 0.0:
            continue

        disc = np.exp(-rate * t)
        intrinsic = disc * (max(f - k, 0.0) if w == "C" else max(k - f, 0.0))
        upper = disc * (f if w == "C" else k)
        # Price must sit strictly inside [intrinsic, discounted bound] for a
        # finite vol to exist. A tiny tolerance keeps deep ITM quotes usable.
        if p <= intrinsic + 1e-12 or p >= upper - 1e-12:
            continue

        def objective(vol, _p=p, _f=f, _k=k, _t=t, _r=rate, _w=w):
            return float(price(_f, _k, _t, vol, _r, _w)) - _p

        try:
            if objective(lo) * objective(hi) > 0.0:
                continue
            out.flat[i] = brentq(objective, lo, hi, xtol=1e-10, rtol=1e-12, maxiter=200)
        except (ValueError, RuntimeError):
            continue

    return float(out[0]) if scalar else out


@dataclass(frozen=True)
class Greeks:
    """Greeks in the published units. See the module docstring."""

    delta: np.ndarray
    gamma: np.ndarray
    vega: np.ndarray  # per volatility point (per 0.01 of sigma)
    vanna: np.ndarray
    volga: np.ndarray
    theta: np.ndarray  # per calendar day
    charm: np.ndarray  # per calendar day

    def as_dict(self) -> dict:
        return asdict(self)


def _delta(F, K, T, sigma, r, is_call):
    d1, _ = d1_d2(F, K, T, sigma)
    disc = np.exp(-r * T)
    return np.where(is_call, disc * norm.cdf(d1), -disc * norm.cdf(-d1))


def greeks(F, K, T, sigma, r, right) -> Greeks:
    """First- and second-order greeks on the forward.

        Delta = e^{-rT} N(d1)                      (call; put by parity)
        Gamma = e^{-rT} phi(d1) / ( F sigma sqrt(T) )
        Vega  = e^{-rT} F phi(d1) sqrt(T)
        Vanna = -e^{-rT} phi(d1) d2 / sigma
        Volga = Vega * d1 * d2 / sigma

    Vanna is dVega/dF and volga is dVega/dsigma; both are derived in the
    methodology writeup rather than asserted. Theta and charm are one-day
    finite differences with the bump in :data:`THETA_BUMP_YEARS`.
    """
    F, K, T, sigma, r = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (F, K, T, sigma, r))
    )
    is_call = np.asarray(right) == "C"

    d1, d2 = d1_d2(F, K, T, sigma)
    disc = np.exp(-r * T)
    sqrt_t = np.sqrt(np.maximum(T, 0.0))
    pdf_d1 = _phi(d1)

    with np.errstate(divide="ignore", invalid="ignore"):
        gamma = disc * pdf_d1 / (F * sigma * sqrt_t)
        vega_raw = disc * F * pdf_d1 * sqrt_t
        vanna = -disc * pdf_d1 * d2 / sigma
        volga = vega_raw * d1 * d2 / sigma

    delta = _delta(F, K, T, sigma, r, is_call)

    # One calendar day forward in time is one bump *less* time to expiry.
    t_bumped = np.maximum(T - THETA_BUMP_YEARS, 0.0)
    theta = price(F, K, t_bumped, sigma, r, right) - price(F, K, T, sigma, r, right)
    charm = _delta(F, K, t_bumped, sigma, r, is_call) - delta

    return Greeks(
        delta=delta,
        gamma=gamma,
        vega=vega_raw / 100.0,  # per volatility point
        vanna=vanna,
        volga=volga,
        theta=theta,  # per calendar day
        charm=charm,  # per calendar day
    )
