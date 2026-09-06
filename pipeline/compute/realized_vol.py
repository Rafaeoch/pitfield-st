"""Realized volatility estimators.

Five estimators at horizons 5, 10, 21, 63 and 252 trading days, all annualised
with 252 trading days. They differ in what information they use — close only,
the daily range, the overnight gap — and publishing them side by side makes the
differences legible rather than hiding behind one number.

We also publish ``sigma_P / sigma_CC``, the ratio of the range-based estimator to
close-to-close. It reads as an intraday-range indicator and essentially nobody
publishes it.
"""

from __future__ import annotations

import numpy as np

TRADING_DAYS = 252
HORIZONS = (5, 10, 21, 63, 252)


def _ann(variance: float) -> float:
    """Annualise a per-day variance and return a volatility."""
    if not np.isfinite(variance) or variance < 0:
        return np.nan
    return float(np.sqrt(variance * TRADING_DAYS))


def close_to_close(close: np.ndarray) -> float:
    """Standard deviation of log returns."""
    close = np.asarray(close, dtype=float)
    if close.size < 3:
        return np.nan
    returns = np.diff(np.log(close))
    return _ann(float(np.var(returns, ddof=1)))


def parkinson(high: np.ndarray, low: np.ndarray) -> float:
    """Parkinson (1980), from the daily range.

        sigma^2_P = 1 / (4 N ln 2) * sum [ ln(H_i / L_i) ]^2
    """
    high, low = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    if high.size < 2:
        return np.nan
    log_hl = np.log(high / low)
    var = np.sum(np.square(log_hl)) / (4.0 * high.size * np.log(2.0))
    return _ann(float(var))


def garman_klass(open, high, low, close) -> float:
    """Garman-Klass (1980).

        sigma^2_GK = 1/N sum { 0.5 [ln(H/L)]^2 - (2 ln2 - 1) [ln(C/O)]^2 }
    """
    o, h, l, c = (np.asarray(v, dtype=float) for v in (open, high, low, close))
    if c.size < 2:
        return np.nan
    log_hl = np.log(h / l)
    log_co = np.log(c / o)
    var = np.mean(0.5 * np.square(log_hl) - (2.0 * np.log(2.0) - 1.0) * np.square(log_co))
    return _ann(float(var))


def rogers_satchell(open, high, low, close) -> float:
    """Rogers-Satchell (1991). Drift-independent, unlike Parkinson and GK.

        sigma^2_RS = 1/N sum { ln(H/C) ln(H/O) + ln(L/C) ln(L/O) }
    """
    o, h, l, c = (np.asarray(v, dtype=float) for v in (open, high, low, close))
    if c.size < 2:
        return np.nan
    var = np.mean(
        np.log(h / c) * np.log(h / o) + np.log(l / c) * np.log(l / o)
    )
    return _ann(float(var))


def yang_zhang(open, high, low, close) -> float:
    """Yang-Zhang (2000). Handles overnight gaps, which the others ignore.

        sigma^2_YZ = sigma^2_overnight + k sigma^2_open_to_close + (1-k) sigma^2_RS
        k = 0.34 / ( 1.34 + (N+1)/(N-1) )

    where sigma^2_overnight uses ln(O_i / C_{i-1}).
    """
    o, h, l, c = (np.asarray(v, dtype=float) for v in (open, high, low, close))
    n = c.size - 1  # one observation lost to the overnight term
    if n < 3:
        return np.nan

    overnight = np.log(o[1:] / c[:-1])
    open_to_close = np.log(c[1:] / o[1:])

    var_overnight = float(np.var(overnight, ddof=1))
    var_oc = float(np.var(open_to_close, ddof=1))

    rs = np.log(h[1:] / c[1:]) * np.log(h[1:] / o[1:]) + np.log(l[1:] / c[1:]) * np.log(
        l[1:] / o[1:]
    )
    var_rs = float(np.mean(rs))

    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    return _ann(var_overnight + k * var_oc + (1.0 - k) * var_rs)


def all_estimators(open, high, low, close, horizons=HORIZONS) -> dict:
    """Every estimator at every horizon, plus the Parkinson/close-to-close ratio.

    Keys are ``{estimator}_{horizon}``, e.g. ``rv_cc_21``. Horizons longer than
    the available history return NaN rather than a short-window estimate
    silently labelled as a long one.
    """
    o, h, l, c = (np.asarray(v, dtype=float) for v in (open, high, low, close))
    out: dict[str, float] = {}

    for n in horizons:
        # +1 bar so that an n-day return window has n returns available.
        window = slice(-(n + 1), None)
        if c.size < n + 1:
            for name in ("cc", "park", "gk", "rs", "yz"):
                out[f"rv_{name}_{n}"] = np.nan
            out[f"rv_park_cc_ratio_{n}"] = np.nan
            continue

        oo, hh, ll, cc = o[window], h[window], l[window], c[window]
        rv_cc = close_to_close(cc)
        rv_park = parkinson(hh, ll)

        out[f"rv_cc_{n}"] = rv_cc
        out[f"rv_park_{n}"] = rv_park
        out[f"rv_gk_{n}"] = garman_klass(oo, hh, ll, cc)
        out[f"rv_rs_{n}"] = rogers_satchell(oo, hh, ll, cc)
        out[f"rv_yz_{n}"] = yang_zhang(oo, hh, ll, cc)
        out[f"rv_park_cc_ratio_{n}"] = (
            rv_park / rv_cc if np.isfinite(rv_cc) and rv_cc > 0 else np.nan
        )

    return out
