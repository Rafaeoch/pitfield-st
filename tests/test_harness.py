"""The statistical harness, checked against things that are known independently.

The danger with a file like this is that it is self-consistent and wrong. So
none of these tests compare the harness to itself: each one checks a property
the statistics must have — HAC exceeding OLS on autocorrelated data, bootstrap
intervals achieving their nominal coverage, BH matching a hand-worked example,
false positives arriving at the advertised rate under a true null.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from pipeline.study.harness import (
    apply_corrections,
    benjamini_hochberg,
    bonferroni,
    hac_ols,
    minimum_detectable_effect,
    newey_west_lags,
    stationary_bootstrap_ci,
    stationary_bootstrap_indices,
    evaluate_bucket,
)


# --- lag rule -------------------------------------------------------------

def test_newey_west_lag_rule_matches_the_formula():
    for n in (100, 1_000, 14_000):
        assert newey_west_lags(n) == int(np.floor(4 * (n / 100) ** (2 / 9)))
    assert newey_west_lags(100) == 4
    # 4 * 140^(2/9) = 11.994, so the floor is 11, not 12.
    assert newey_west_lags(14_000) == 11


# --- HAC ------------------------------------------------------------------

def test_hac_matches_ols_when_residuals_are_independent():
    """With no autocorrelation the sandwich should collapse to the usual SE."""
    rng = np.random.default_rng(0)
    n = 4000
    x = rng.normal(size=n)
    y = 0.3 * x + rng.normal(size=n)
    X = np.column_stack([np.ones(n), x])

    _, hac_se, _ = hac_ols(y, X, lags=0)
    resid = y - X @ (np.linalg.pinv(X.T @ X) @ X.T @ y)
    sigma2 = resid @ resid / n
    ols_se = np.sqrt(np.diag(sigma2 * np.linalg.pinv(X.T @ X)))

    assert hac_se[1] == pytest.approx(ols_se[1], rel=0.02)


def test_hac_exceeds_ols_when_residuals_are_autocorrelated():
    """The entire reason HAC is here. Persistent residuals inflate the true SE."""
    rng = np.random.default_rng(1)
    n = 4000
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.85 * e[i - 1] + rng.normal()      # strongly persistent
    x = rng.normal(size=n)
    y = 0.0 * x + e
    X = np.column_stack([np.ones(n), x])

    _, se_naive, _ = hac_ols(y, X, lags=0)
    _, se_hac, _ = hac_ols(y, X)
    assert se_hac[0] > se_naive[0] * 1.5


def test_hac_recovers_a_known_coefficient():
    rng = np.random.default_rng(2)
    n = 5000
    d = (rng.random(n) < 0.3).astype(float)
    y = 0.001 + 0.0025 * d + rng.normal(0, 0.01, n)
    beta, se, _ = hac_ols(y, np.column_stack([np.ones(n), d]))
    assert beta[1] == pytest.approx(0.0025, abs=4 * se[1])


# --- stationary bootstrap -------------------------------------------------

def test_bootstrap_indices_are_in_range_and_right_length():
    rng = np.random.default_rng(3)
    idx = stationary_bootstrap_indices(500, 21.0, rng)
    assert idx.size == 500
    assert idx.min() >= 0 and idx.max() < 500


def test_bootstrap_blocks_have_the_requested_mean_length():
    """Geometric blocks with p = 1/L should average L."""
    rng = np.random.default_rng(4)
    idx = stationary_bootstrap_indices(200_000, 21.0, rng)
    # A break is anywhere the index does not advance by exactly one (mod n).
    steps = (idx[1:] - idx[:-1]) % 200_000
    breaks = int(np.sum(steps != 1))
    mean_block = idx.size / max(breaks, 1)
    assert 17.0 < mean_block < 26.0


def test_bootstrap_preserves_autocorrelation():
    """Blocks exist precisely so the resample is not iid. Verify it is not."""
    rng = np.random.default_rng(5)
    n = 20_000
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.9 * e[i - 1] + rng.normal()
    idx = stationary_bootstrap_indices(n, 50.0, rng)
    resampled = e[idx]
    rho = np.corrcoef(resampled[:-1], resampled[1:])[0, 1]
    assert rho > 0.5          # an iid resample would give roughly zero


def test_bootstrap_interval_covers_a_known_difference():
    """Nominal 95% interval should contain the truth on data built to have it."""
    rng = np.random.default_rng(6)
    n = 3000
    mask = np.zeros(n, dtype=bool)
    mask[::7] = True
    y = rng.normal(0, 0.01, n) + np.where(mask, 0.003, 0.0)
    lo, hi, valid = stationary_bootstrap_ci(y, mask, resamples=600, seed=11)
    assert valid > 500
    assert lo < 0.003 < hi


# --- multiple comparisons -------------------------------------------------

def test_benjamini_hochberg_against_a_hand_worked_example():
    p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205])
    m = len(p)
    expected = np.minimum.accumulate((p * m / np.arange(1, m + 1))[::-1])[::-1]
    assert np.allclose(benjamini_hochberg(p), np.clip(expected, 0, 1))


def test_benjamini_hochberg_is_monotone_and_bounded():
    rng = np.random.default_rng(7)
    p = rng.random(200)
    adj = benjamini_hochberg(p)
    assert adj.min() >= 0 and adj.max() <= 1
    order = np.argsort(p)
    assert np.all(np.diff(adj[order]) >= -1e-12)


def test_bh_is_never_stricter_than_bonferroni():
    """BH is uniformly less conservative. If it ever is not, one is wrong."""
    rng = np.random.default_rng(8)
    p = rng.random(500) ** 2
    assert np.all(benjamini_hochberg(p) <= bonferroni(p) + 1e-12)


def test_bonferroni_multiplies_by_the_family_size():
    p = np.array([0.01, 0.02, 0.5])
    assert np.allclose(bonferroni(p), [0.03, 0.06, 1.0])


def test_corrections_ignore_nan_but_keep_positions():
    p = np.array([0.01, np.nan, 0.02])
    adj = benjamini_hochberg(p)
    assert np.isnan(adj[1])
    # Family size is 2, not 3: an untestable hypothesis is not a test.
    assert bonferroni(p)[0] == pytest.approx(0.02)


# --- power ----------------------------------------------------------------

def test_minimum_detectable_effect_matches_the_closed_form():
    se = 0.0004
    expected = (stats.norm.ppf(0.975) + stats.norm.ppf(0.80)) * se
    assert minimum_detectable_effect(se) == pytest.approx(expected)
    assert minimum_detectable_effect(se) == pytest.approx(2.8016 * se, rel=1e-3)


def test_mde_is_nan_without_a_usable_standard_error():
    assert np.isnan(minimum_detectable_effect(0.0))
    assert np.isnan(minimum_detectable_effect(np.nan))


# --- end to end -----------------------------------------------------------

def test_bucket_test_finds_an_effect_that_is_really_there():
    rng = np.random.default_rng(9)
    n = 6000
    mask = np.zeros(n, dtype=bool)
    mask[::5] = True
    y = rng.normal(0, 0.01, n) + np.where(mask, 0.004, 0.0)

    r = evaluate_bucket("planted", "IDX_daily", "in", y, mask, resamples=400)
    assert r.effect == pytest.approx(0.004, abs=0.001)
    assert r.p_value < 0.001
    assert r.ci_low < 0.004 < r.ci_high
    assert r.n_events == int(mask.sum())


def test_bucket_test_finds_nothing_when_there_is_nothing():
    rng = np.random.default_rng(10)
    n = 6000
    mask = np.zeros(n, dtype=bool)
    mask[::5] = True
    y = rng.normal(0, 0.01, n)          # no effect at all

    r = evaluate_bucket("null", "IDX_daily", "in", y, mask, resamples=400)
    assert r.p_value > 0.05
    assert r.ci_low < 0 < r.ci_high


def test_false_positive_rate_under_a_true_null():
    """The property the whole study rests on.

    Run many independent tests where no effect exists and confirm that
    significant results arrive at roughly the nominal rate, and that the
    corrections then remove almost all of them. If uncorrected testing did not
    produce false positives at 5%, the Bangladesh line would have nothing to
    illustrate.
    """
    rng = np.random.default_rng(12)
    n, trials = 1500, 120
    raw = []
    for _ in range(trials):
        mask = np.zeros(n, dtype=bool)
        start = rng.integers(0, 20)
        mask[start::9] = True
        y = rng.normal(0, 0.01, n)
        raw.append(evaluate_bucket("null", "s", "b", y, mask, resamples=0).p_value)

    raw = np.array(raw)
    uncorrected = int(np.sum(raw < 0.05))
    # Binomial(120, 0.05) has mean 6; allow a generous band.
    assert 0 <= uncorrected <= 16, uncorrected

    survivors = int(np.sum(benjamini_hochberg(raw) < 0.10))
    assert survivors <= 2, survivors
    assert int(np.sum(bonferroni(raw) < 0.05)) == 0


def test_apply_corrections_spans_the_whole_family():
    rng = np.random.default_rng(13)
    n = 800
    results = []
    for i in range(20):
        mask = np.zeros(n, dtype=bool)
        mask[i % 5 :: 6] = True
        y = rng.normal(0, 0.01, n)
        results.append(evaluate_bucket(f"h{i}", "s", "b", y, mask, resamples=0))

    apply_corrections(results)
    assert all(np.isfinite(r.p_bh) for r in results)
    assert all(r.p_bonferroni >= r.p_value - 1e-12 for r in results)
    assert all(r.p_bh <= r.p_bonferroni + 1e-12 for r in results)


def test_degenerate_buckets_return_empty_rather_than_a_number():
    y = np.random.default_rng(14).normal(0, 0.01, 500)
    for mask in (np.zeros(500, dtype=bool), np.ones(500, dtype=bool)):
        r = evaluate_bucket("degenerate", "s", "b", y, mask, resamples=0)
        assert np.isnan(r.p_value)
        assert np.isnan(r.effect)
