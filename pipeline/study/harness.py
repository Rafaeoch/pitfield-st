"""The statistical harness. One treatment, applied identically to every hypothesis.

No per-hypothesis tuning happens anywhere in this file, and that is the whole
design. A harness with a knob per hypothesis is a machine for finding effects.

Four pieces, each of which can be checked against something known:

* **Newey-West HAC standard errors.** Daily returns are close to uncorrelated
  but bucket membership is strongly persistent — a lunar phase lasts days, a
  retrograde lasts weeks — so the residuals of a bucket regression are not
  independent and ordinary standard errors are too small.
* **Stationary bootstrap** (Politis & Romano 1994). Resamples blocks of
  geometrically distributed length so the resampled series keeps the
  autocorrelation of the original. Fixed-length blocks would not be stationary.
* **Benjamini-Hochberg and Bonferroni**, applied across the *entire* family.
  Both are reported: they disagree, and the disagreement is the point.
* **Minimum detectable effect** at 80% power given the events that exist. For
  most of this list that number is the result.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
from scipy import stats

# Politis & Romano's expected block length, in trading days. One month of
# sessions: long enough to carry the persistence of a bucket, short enough that
# a 14,000-day sample still contains many independent blocks.
DEFAULT_BLOCK_LENGTH = 21.0

DEFAULT_RESAMPLES = 10_000
DEFAULT_POWER = 0.80
DEFAULT_ALPHA = 0.05


def newey_west_lags(n: int) -> int:
    """floor(4 (n/100)^(2/9)), the rule the pre-registration fixes."""
    if n <= 1:
        return 0
    return int(np.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))


def hac_ols(y: np.ndarray, X: np.ndarray, lags: int | None = None):
    """OLS with Newey-West standard errors.

    Returns (beta, se, lags_used). ``X`` must already include an intercept
    column. The sandwich is

        (X'X)^-1  S  (X'X)^-1,
        S = sum_{l=-L}^{L} w_l  sum_t  u_t u_{t-l} x_t x_{t-l}'

    with Bartlett weights w_l = 1 - |l|/(L+1), which is what guarantees S is
    positive semi-definite.
    """
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    n, k = X.shape
    if lags is None:
        lags = newey_west_lags(n)

    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    resid = y - X @ beta

    # Lag zero.
    u = resid[:, None] * X
    S = u.T @ u
    # Bartlett-weighted cross products.
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)
        gamma = u[lag:].T @ u[:-lag]
        S += w * (gamma + gamma.T)

    cov = xtx_inv @ S @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0.0))
    return beta, se, lags


def stationary_bootstrap_indices(
    n: int, block_length: float, rng: np.random.Generator
) -> np.ndarray:
    """One resample's worth of indices, blocks geometric with mean ``block_length``.

    Wraps at the end of the series, which is what makes the resampled process
    stationary — the defining property of this bootstrap.
    """
    if n == 0:
        return np.array([], dtype=int)
    p = 1.0 / max(block_length, 1.0)

    # Vectorised rather than looped: the loop form costs about 30ms per
    # resample, which at ten thousand resamples across a hundred and eighty
    # buckets is several hours of pure interpreter overhead. This builds the
    # same process — geometric block lengths, uniform block starts, wrapping —
    # with three passes over the array.
    new_block = rng.random(n) < p
    new_block[0] = True
    starts = rng.integers(0, n, size=n)

    position = np.arange(n)
    # Index of the position at which the current block began.
    block_start = np.maximum.accumulate(np.where(new_block, position, 0))
    offset = position - block_start
    return (starts[block_start] + offset) % n


def stationary_bootstrap_ci(
    y: np.ndarray,
    mask: np.ndarray,
    *,
    resamples: int = DEFAULT_RESAMPLES,
    block_length: float = DEFAULT_BLOCK_LENGTH,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 20260902,
):
    """Percentile CI for (mean in bucket) - (mean outside), by stationary bootstrap.

    Returns (lo, hi, n_valid). Resamples that land entirely inside or entirely
    outside the bucket produce no difference and are discarded rather than
    counted as zero — treating them as zero would drag the interval toward the
    null and understate uncertainty.
    """
    y = np.asarray(y, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    n = y.size
    rng = np.random.default_rng(seed)

    diffs = np.empty(resamples, dtype=float)
    valid = 0
    for b in range(resamples):
        idx = stationary_bootstrap_indices(n, block_length, rng)
        m = mask[idx]
        if m.all() or not m.any():
            continue
        sample = y[idx]
        diffs[valid] = sample[m].mean() - sample[~m].mean()
        valid += 1

    if valid < 100:
        return np.nan, np.nan, valid
    lo, hi = np.percentile(diffs[:valid], [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi), valid


def benjamini_hochberg(pvalues) -> np.ndarray:
    """BH step-up adjusted p-values, in the input order.

    p_adj_(i) = min_{j >= i} ( m/j * p_(j) ), capped at 1 and made monotone.
    """
    p = np.asarray(pvalues, dtype=float)
    finite = np.isfinite(p)
    out = np.full(p.shape, np.nan)
    if not finite.any():
        return out

    q = p[finite]
    m = q.size
    order = np.argsort(q)
    ranked = q[order]
    adjusted = ranked * m / np.arange(1, m + 1)
    # Enforce monotonicity from the largest p downward.
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)

    restored = np.empty(m)
    restored[order] = adjusted
    out[finite] = restored
    return out


def bonferroni(pvalues) -> np.ndarray:
    """Bonferroni adjusted p-values over the finite entries."""
    p = np.asarray(pvalues, dtype=float)
    finite = np.isfinite(p)
    out = np.full(p.shape, np.nan)
    out[finite] = np.clip(p[finite] * finite.sum(), 0.0, 1.0)
    return out


def minimum_detectable_effect(
    se: float, *, power: float = DEFAULT_POWER, alpha: float = DEFAULT_ALPHA
) -> float:
    """Smallest true effect detectable at ``power``, two-sided, given ``se``.

        MDE = ( z_{1-alpha/2} + z_{power} ) * se

    Reported beside every result. When it exceeds any effect the literature has
    ever claimed, the test had no chance of finding anything and that is the
    honest finding.
    """
    if not np.isfinite(se) or se <= 0:
        return np.nan
    z_alpha = stats.norm.ppf(1.0 - alpha / 2.0)
    z_power = stats.norm.ppf(power)
    return float((z_alpha + z_power) * se)


@dataclass
class BucketResult:
    """One bucket of one hypothesis against one return series."""

    hypothesis: str
    series: str
    bucket: str
    n_observations: int
    n_events: int
    mean_in: float
    mean_out: float
    effect: float
    se: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    hac_lags: int
    mde: float
    p_bh: float = float("nan")
    p_bonferroni: float = float("nan")

    def as_dict(self) -> dict:
        d = asdict(self)
        return {k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                for k, v in d.items()}


# Named `evaluate_bucket`, not `test_bucket`. A public function whose name
# begins with "test_" is collected by pytest as a test case and invoked with
# fixtures it never asked for, which fails noisily in an unrelated file. The
# statistical sense of "test" loses to the convention here.
def evaluate_bucket(
    hypothesis: str,
    series: str,
    bucket: str,
    y: np.ndarray,
    mask: np.ndarray,
    *,
    resamples: int = DEFAULT_RESAMPLES,
    block_length: float = DEFAULT_BLOCK_LENGTH,
    seed: int = 20260902,
) -> BucketResult:
    """Difference in mean return, bucket against its complement.

    Framed as a regression on a dummy so the HAC sandwich applies directly to
    the difference, rather than being pieced together from two separate means.
    """
    y = np.asarray(y, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    keep = np.isfinite(y)
    y, mask = y[keep], mask[keep]

    n = y.size
    n_events = int(mask.sum())
    empty = BucketResult(
        hypothesis, series, bucket, n, n_events,
        np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, 0, np.nan,
    )
    if n_events == 0 or n_events == n or n < 30:
        return empty

    X = np.column_stack([np.ones(n), mask.astype(float)])
    beta, se, lags = hac_ols(y, X)
    effect, effect_se = float(beta[1]), float(se[1])

    if not np.isfinite(effect_se) or effect_se <= 0:
        return empty

    t_stat = effect / effect_se
    # Normal reference: with N in the thousands the t and z tails coincide, and
    # the HAC correction has already absorbed the dependence that would
    # otherwise justify a smaller effective sample.
    p_value = float(2.0 * stats.norm.sf(abs(t_stat)))

    ci_low, ci_high, _ = stationary_bootstrap_ci(
        y, mask, resamples=resamples, block_length=block_length, seed=seed
    )

    return BucketResult(
        hypothesis=hypothesis,
        series=series,
        bucket=bucket,
        n_observations=n,
        n_events=n_events,
        mean_in=float(y[mask].mean()),
        mean_out=float(y[~mask].mean()),
        effect=effect,
        se=effect_se,
        t_stat=float(t_stat),
        p_value=p_value,
        ci_low=ci_low,
        ci_high=ci_high,
        hac_lags=int(lags),
        mde=minimum_detectable_effect(effect_se),
    )


def apply_corrections(results: list[BucketResult]) -> list[BucketResult]:
    """Correct across the entire family, in place, and return the same list.

    Every bucket of every hypothesis against every return series counts as one
    test. Correcting within a hypothesis, or within a tier, would be the same
    fishing expedition wearing a correction as a disguise.
    """
    p = np.array([r.p_value for r in results], dtype=float)
    bh = benjamini_hochberg(p)
    bonf = bonferroni(p)
    for r, a, b in zip(results, bh, bonf):
        r.p_bh = float(a)
        r.p_bonferroni = float(b)
    return results
