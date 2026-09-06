"""Metrics read off a calibrated surface: constant-maturity IV, skew, term structure.

Two things here are easy to get wrong and are the usual tell that a surface was
assembled rather than fitted:

  * **Constant-maturity IV interpolates in total variance, not in IV.** Total
    variance is the additive quantity across time; interpolating volatility
    directly is wrong and produces a term structure that sags between listed
    expiries.
  * **Delta-referenced skew has to be solved on the smile**, because the strike
    at 25 delta depends on the volatility there, which depends on the strike.
    We root-find rather than approximating with an ATM volatility.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from .svi import SVIParams

CONSTANT_MATURITIES = (30, 60, 90)
DAYS_PER_YEAR = 365.0

# Root-find for the delta-referenced strikes over this log-moneyness range.
_K_BRACKET = (-1.5, 1.5)


def atm_total_variance(fits: list[tuple[float, SVIParams]]) -> tuple[np.ndarray, np.ndarray]:
    """(maturities, ATM total variance) sorted by maturity, for interpolation."""
    ordered = sorted(fits, key=lambda item: item[0])
    t = np.array([T for T, _ in ordered], dtype=float)
    w = np.array([float(p.total_variance(0.0)) for _, p in ordered], dtype=float)
    return t, w


def constant_maturity_iv(
    fits: list[tuple[float, SVIParams]], days: int
) -> float:
    """ATM implied vol at a constant maturity, interpolated in total variance.

    Returns NaN when the target sits outside the listed expiry range — we do not
    extrapolate a surface past the last expiry the market actually quotes.
    """
    if len(fits) < 2:
        return np.nan
    target_t = days / DAYS_PER_YEAR
    t, w = atm_total_variance(fits)

    finite = np.isfinite(t) & np.isfinite(w)
    t, w = t[finite], w[finite]
    if t.size < 2 or target_t < t[0] or target_t > t[-1]:
        return np.nan

    w_interp = float(np.interp(target_t, t, w))
    if w_interp <= 0:
        return np.nan
    return float(np.sqrt(w_interp / target_t))


def _strike_at_delta(
    params: SVIParams, T: float, r: float, target_delta: float, right: str
) -> float:
    """Log-moneyness k whose Black-76 delta equals ``target_delta``.

    Solved on the fitted smile: sigma is re-read from the surface at each
    candidate k, so this is the true delta-referenced strike rather than an
    ATM-vol approximation.
    """
    disc = np.exp(-r * T)

    def delta_at(k: float) -> float:
        sigma = float(params.implied_vol(k, T))
        if not np.isfinite(sigma) or sigma <= 0:
            return np.nan
        # k = ln(K/F), so ln(F/K) = -k.
        d1 = (-k + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
        return disc * norm.cdf(d1) if right == "C" else -disc * norm.cdf(-d1)

    def objective(k: float) -> float:
        d = delta_at(k)
        return np.nan if not np.isfinite(d) else d - target_delta

    lo, hi = _K_BRACKET
    try:
        f_lo, f_hi = objective(lo), objective(hi)
        if not (np.isfinite(f_lo) and np.isfinite(f_hi)) or f_lo * f_hi > 0:
            return np.nan
        return float(brentq(objective, lo, hi, xtol=1e-10, maxiter=200))
    except (ValueError, RuntimeError):
        return np.nan


def wing_skew(params: SVIParams, T: float, r: float) -> dict:
    """Skew at 10 delta as well as 25.

    The 25-delta pair describes the body of the smile. The 10-delta pair
    describes the wings, which is where crash pricing lives and where the two
    can diverge sharply — a chain can look calm at 25 delta and be paying up
    heavily at 10.
    """
    out: dict = {}
    for d in (0.10, 0.25):
        m = skew_metrics(params, T, r, d)
        tag = f"{int(d * 100)}d"
        out[f"risk_reversal_{tag}"] = m[f"risk_reversal_25d"] if d == 0.25 else (
            m[f"iv_{int(d*100)}d_put"] - m[f"iv_{int(d*100)}d_call"]
        )
        out[f"butterfly_{tag}"] = m["butterfly_25d"] if d == 0.25 else (
            (m[f"iv_{int(d*100)}d_put"] + m[f"iv_{int(d*100)}d_call"]) / 2.0 - m["iv_atm"]
        )
    out["skew_steepness"] = out.get("risk_reversal_10d", np.nan) - out.get(
        "risk_reversal_25d", np.nan
    )
    return out


def skew_metrics(params: SVIParams, T: float, r: float, delta: float = 0.25) -> dict:
    """25-delta risk reversal, butterfly, and the ATM skew slope.

    ATM skew slope is d(sigma)/dk at k = 0, taken analytically from the fitted
    SVI. With sigma = sqrt(w/T),

        d(sigma)/dk = w'(k) / ( 2 T sigma(k) )
    """
    k_put = _strike_at_delta(params, T, r, -delta, "P")
    k_call = _strike_at_delta(params, T, r, delta, "C")

    iv_put = float(params.implied_vol(k_put, T)) if np.isfinite(k_put) else np.nan
    iv_call = float(params.implied_vol(k_call, T)) if np.isfinite(k_call) else np.nan
    iv_atm = float(params.implied_vol(0.0, T))

    sigma_atm = iv_atm
    w_prime = float(params.d_total_variance(0.0))
    slope = w_prime / (2.0 * T * sigma_atm) if sigma_atm > 0 else np.nan

    return {
        "iv_atm": iv_atm,
        f"iv_{int(delta * 100)}d_put": iv_put,
        f"iv_{int(delta * 100)}d_call": iv_call,
        "risk_reversal_25d": iv_put - iv_call,
        "butterfly_25d": (iv_put + iv_call) / 2.0 - iv_atm,
        "atm_skew_slope": slope,
    }


def term_structure(fits: list[tuple[float, SVIParams]]) -> dict:
    """Constant-maturity IVs plus the 90-30 slope and a shape flag."""
    out = {
        f"iv_{d}": constant_maturity_iv(fits, d) for d in CONSTANT_MATURITIES
    }
    iv30, iv90 = out.get("iv_30", np.nan), out.get("iv_90", np.nan)
    slope = iv90 - iv30 if np.isfinite(iv30) and np.isfinite(iv90) else np.nan
    out["term_slope_90_30"] = slope
    out["term_shape"] = (
        "contango" if np.isfinite(slope) and slope > 0
        else "backwardation" if np.isfinite(slope) and slope < 0
        else "flat"
    )
    return out


def forward_volatility(t1: float, sigma1: float, t2: float, sigma2: float) -> float:
    """Volatility implied between two future dates, from two spot volatilities.

        sigma_fwd = sqrt( (sigma2^2 T2 - sigma1^2 T1) / (T2 - T1) )

    Total variance is additive in time, so the variance between T1 and T2 is
    the difference of the two total variances. This is what the market is
    charging for the period *between* two expiries rather than from today, and
    it is where a term structure that looks smooth in spot terms often turns
    out to imply something strange.

    Returns NaN when the difference is negative — that is a calendar arbitrage,
    and the honest response is to report nothing rather than the square root of
    a negative number.
    """
    if not all(np.isfinite([t1, sigma1, t2, sigma2])) or t2 <= t1 or t1 < 0:
        return np.nan
    variance = (sigma2**2 * t2 - sigma1**2 * t1) / (t2 - t1)
    return float(np.sqrt(variance)) if variance > 0 else np.nan


def implied_move(sigma: float, T: float) -> float:
    """The one-standard-deviation move an expiry is pricing, as a fraction.

        move = sigma * sqrt(T)

    The number every earnings preview quotes, stated as what it is: a one-sigma
    width under a lognormal assumption, not a forecast and not a range the
    price is expected to respect.
    """
    if not np.isfinite(sigma) or not np.isfinite(T) or T <= 0 or sigma <= 0:
        return np.nan
    return float(sigma * np.sqrt(T))


def iv_rank_and_percentile(iv_today: float, history: np.ndarray) -> dict:
    """IV rank and percentile against a trailing window (252 days by convention).

        rank       = (IV30 - min252) / (max252 - min252)
        percentile = share of the window strictly below today
    """
    history = np.asarray(history, dtype=float)
    history = history[np.isfinite(history)]
    if history.size < 20 or not np.isfinite(iv_today):
        return {"iv_rank": np.nan, "iv_percentile": np.nan, "iv_history_n": int(history.size)}

    lo, hi = float(history.min()), float(history.max())
    rank = (iv_today - lo) / (hi - lo) if hi > lo else np.nan
    percentile = float(np.mean(history < iv_today))
    return {
        "iv_rank": rank,
        "iv_percentile": percentile,
        "iv_history_n": int(history.size),
    }


def variance_risk_premium(iv30_then: float, rv30_realized: float) -> float:
    """VRP = IV30(t) - RV30(t -> t+30).

    Only computable 30 days after the fact. Callers must publish it lagged and
    label it as such; a forward-looking VRP presented as known is a lookahead
    bug wearing a metric's clothes.
    """
    if not (np.isfinite(iv30_then) and np.isfinite(rv30_realized)):
        return np.nan
    return float(iv30_then - rv30_realized)
