"""Model-free implied variance — the VIX methodology, applied to any chain.

This is the most useful thing a chain can tell you that a parametric fit
cannot. SVI gives an at-the-money volatility conditional on a five-parameter
functional form being right. The model-free variance swap rate assumes no model
at all: it is a weighted integral over every out-of-the-money option, and it is
what a variance swap on that expiry is actually worth.

Publishing both is the point. When they agree, the parametric fit is doing no
violence to the data. When they diverge, the wings are telling you something
the smile's functional form cannot express, and the divergence is a diagnostic
worth having rather than a discrepancy to hide.

The formula (CBOE white paper, and Demeterfi-Derman-Kamal-Zou before it):

    sigma^2 = (2/T) SUM_i (dK_i / K_i^2) e^{rT} Q(K_i)  -  (1/T) (F/K_0 - 1)^2

where Q(K_i) is the midpoint of the *out-of-the-money* option at strike K_i,
K_0 is the highest strike at or below the forward, dK_i is the half-distance to
the neighbouring strikes, and the final term corrects for the forward not
landing exactly on a listed strike.

Two details that are easy to get wrong and are handled explicitly:

* **Only OTM options enter.** At K_0 itself the average of the call and the put
  is used, because neither is unambiguously out of the money there.
* **The strike grid is not uniform.** dK is the half-distance to the
  neighbours, and at the two ends it is the single available gap. Treating dK
  as a constant biases the result by however non-uniform the grid is, which for
  a real chain with mixed 1-point and 5-point increments is substantial.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ModelFreeVariance:
    total_variance: float      # sigma^2 * T
    volatility: float          # annualised
    n_strikes: int
    k0: float                  # the strike bracketing the forward
    truncated_low: bool        # did the usable strip stop before the wing died?
    truncated_high: bool

    def as_dict(self) -> dict:
        return {
            "mfiv_total_variance": self.total_variance,
            "mfiv_volatility": self.volatility,
            "mfiv_n_strikes": self.n_strikes,
            "mfiv_k0": self.k0,
            "mfiv_truncated_low": self.truncated_low,
            "mfiv_truncated_high": self.truncated_high,
        }


def _empty() -> ModelFreeVariance:
    return ModelFreeVariance(np.nan, np.nan, 0, np.nan, False, False)


def model_free_variance(
    strikes: np.ndarray,
    call_mid: np.ndarray,
    put_mid: np.ndarray,
    forward: float,
    rate: float,
    T: float,
) -> ModelFreeVariance:
    """Variance swap rate for one expiry, from the OTM strip.

    Inputs are aligned arrays: ``call_mid[i]`` and ``put_mid[i]`` are the mid
    prices at ``strikes[i]``, NaN where there is no two-sided market.
    """
    strikes = np.asarray(strikes, dtype=float)
    call_mid = np.asarray(call_mid, dtype=float)
    put_mid = np.asarray(put_mid, dtype=float)

    if T <= 0 or not np.isfinite(forward) or forward <= 0 or strikes.size < 3:
        return _empty()

    order = np.argsort(strikes)
    strikes, call_mid, put_mid = strikes[order], call_mid[order], put_mid[order]

    # K0: the highest strike at or below the forward.
    below = strikes[strikes <= forward]
    if below.size == 0:
        return _empty()
    k0 = float(below[-1])

    # Assemble the out-of-the-money strip: puts below K0, calls above, and the
    # average of the two at K0 where neither is clearly out of the money.
    price = np.full(strikes.shape, np.nan)
    lower = strikes < k0
    upper = strikes > k0
    at = strikes == k0

    price[lower] = put_mid[lower]
    price[upper] = call_mid[upper]
    both = call_mid[at], put_mid[at]
    price[at] = np.where(
        np.isfinite(both[0]) & np.isfinite(both[1]),
        (both[0] + both[1]) / 2.0,
        np.where(np.isfinite(both[1]), both[1], both[0]),
    )

    usable = np.isfinite(price) & (price > 0)
    if usable.sum() < 3:
        return _empty()

    # CBOE stops the strip at two consecutive zero-bid strikes. We keep only
    # the contiguous run around K0 for the same reason: past the point where
    # quotes die, the strip is not measuring the tail, it is measuring the
    # absence of a market.
    idx = np.where(usable)[0]
    k0_pos = int(np.argmin(np.abs(strikes - k0)))
    if not usable[k0_pos]:
        return _empty()

    lo = k0_pos
    while lo - 1 >= 0 and usable[lo - 1]:
        lo -= 1
    hi = k0_pos
    while hi + 1 < strikes.size and usable[hi + 1]:
        hi += 1

    ks = strikes[lo : hi + 1]
    qs = price[lo : hi + 1]
    if ks.size < 3:
        return _empty()

    # dK: half the distance to each neighbour; the single gap at the ends.
    dk = np.empty_like(ks)
    dk[1:-1] = (ks[2:] - ks[:-2]) / 2.0
    dk[0] = ks[1] - ks[0]
    dk[-1] = ks[-1] - ks[-2]

    contribution = (dk / np.square(ks)) * np.exp(rate * T) * qs
    total = (2.0 / T) * contribution.sum() - (1.0 / T) * (forward / k0 - 1.0) ** 2
    total_variance = total * T

    return ModelFreeVariance(
        total_variance=float(total_variance),
        volatility=float(np.sqrt(max(total, 0.0))),
        n_strikes=int(ks.size),
        k0=k0,
        # The strip was cut short by our own filter rather than by the wing
        # genuinely dying out. Worth publishing: a truncated strip understates
        # variance, always.
        truncated_low=bool(lo > 0 and not usable[lo - 1] and lo > idx.min()),
        truncated_high=bool(hi < strikes.size - 1 and hi < idx.max()),
    )


def interpolate_30_day(
    per_expiry: list[tuple[float, float]], target_days: float = 30.0
) -> float:
    """Constant-maturity model-free volatility, interpolated in total variance.

    ``per_expiry`` is [(T, total_variance)]. Same rule as everywhere else in
    this pipeline: interpolate the additive quantity, then convert back. And
    the same refusal to extrapolate past the listed expiries.
    """
    if len(per_expiry) < 2:
        return np.nan
    target = target_days / 365.0
    pairs = sorted(per_expiry)
    t = np.array([p[0] for p in pairs])
    w = np.array([p[1] for p in pairs])
    ok = np.isfinite(t) & np.isfinite(w)
    t, w = t[ok], w[ok]
    # A hair of floating point should not count as extrapolation: 30/365 is
    # 0.08219178, and an expiry listed at 0.0822 is the same expiry. The
    # tolerance is a few minutes of a year, far smaller than any real gap
    # between listed expiries, so this cannot silently extrapolate.
    tol = 1e-4
    if t.size < 2 or target < t[0] - tol or target > t[-1] + tol:
        return np.nan
    target = float(np.clip(target, t[0], t[-1]))
    interp = float(np.interp(target, t, w))
    return float(np.sqrt(max(interp, 0.0) / target))
