"""SVI: analytic derivatives, the no-arbitrage diagnostics, and calibration recovery.

A note on what the calibration test asserts. Raw SVI is not uniquely identified —
different parameter vectors can produce the same curve to within numerical
tolerance — so asserting parameter-by-parameter recovery would be testing an
artefact of the optimiser, not the fit. The test compares the fitted *curve* to
the true curve on a dense grid, which is the quantity the surface actually
publishes.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.compute.svi import (
    CHECK_GRID,
    SVIParams,
    calibrate,
    check_butterfly,
    check_calendar,
    durrleman_g,
)

# A well-behaved equity smile: negative skew, positive minimum variance.
GOOD = SVIParams(a=0.008, b=0.055, rho=-0.65, m=0.02, sigma=0.22)
T = 0.25


def test_analytic_first_derivative_matches_numerical():
    k = np.linspace(-1.0, 1.0, 51)
    h = 1e-6
    numeric = (GOOD.total_variance(k + h) - GOOD.total_variance(k - h)) / (2 * h)
    assert np.allclose(GOOD.d_total_variance(k), numeric, rtol=1e-6)


def test_analytic_second_derivative_matches_numerical():
    k = np.linspace(-1.0, 1.0, 51)
    h = 1e-4
    numeric = (
        GOOD.total_variance(k + h) - 2 * GOOD.total_variance(k) + GOOD.total_variance(k - h)
    ) / (h * h)
    assert np.allclose(GOOD.d2_total_variance(k), numeric, rtol=1e-5, atol=1e-9)


def test_minimum_total_variance_formula():
    """w_min = a + b sigma sqrt(1 - rho^2), derived in the module docstring."""
    dense = np.linspace(-8.0, 8.0, 200_001)
    assert GOOD.min_total_variance == pytest.approx(
        float(np.min(GOOD.total_variance(dense))), rel=1e-6
    )


def test_total_variance_is_positive_when_constraint_holds():
    assert GOOD.min_total_variance > 0
    assert np.all(GOOD.total_variance(CHECK_GRID) > 0)


def test_good_surface_passes_durrleman():
    ok, min_g = check_butterfly(GOOD)
    assert ok
    assert min_g >= 0


def test_arbitrageable_surface_is_detected():
    """A steep, over-curved smile implies negative density and must be caught."""
    bad = SVIParams(a=0.0005, b=0.85, rho=-0.97, m=0.0, sigma=0.03)
    ok, min_g = check_butterfly(bad)
    assert not ok
    assert min_g < 0
    assert np.any(durrleman_g(bad) < 0)


def test_calendar_condition_accepts_increasing_total_variance():
    fits = [
        (0.08, SVIParams(a=0.0026, b=0.030, rho=-0.6, m=0.0, sigma=0.20)),
        (0.25, SVIParams(a=0.0082, b=0.055, rho=-0.6, m=0.0, sigma=0.22)),
        (0.50, SVIParams(a=0.0165, b=0.080, rho=-0.6, m=0.0, sigma=0.25)),
    ]
    result = check_calendar(fits)
    ok, n_violations = result.ok, result.n_violations
    assert ok
    assert n_violations == 0


def test_calendar_condition_catches_decreasing_total_variance():
    """Total variance falling with maturity is a calendar arbitrage."""
    fits = [
        (0.25, SVIParams(a=0.020, b=0.055, rho=-0.6, m=0.0, sigma=0.22)),
        (0.50, SVIParams(a=0.004, b=0.030, rho=-0.6, m=0.0, sigma=0.22)),
    ]
    result = check_calendar(fits)
    ok, n_violations = result.ok, result.n_violations
    assert not ok
    assert n_violations > 0


def test_calendar_check_is_order_independent():
    fits = [
        (0.50, SVIParams(a=0.0165, b=0.080, rho=-0.6, m=0.0, sigma=0.25)),
        (0.08, SVIParams(a=0.0026, b=0.030, rho=-0.6, m=0.0, sigma=0.20)),
    ]
    assert check_calendar(fits).ok


def test_calibration_recovers_the_curve():
    """Fit to noiseless total variance from a known surface; recover that surface."""
    k = np.linspace(-0.55, 0.45, 60)
    w_true = GOOD.total_variance(k)

    fit = calibrate(k, w_true, T)
    assert fit.converged

    # Compare only where the data constrained the fit. SVI makes no promise
    # about extrapolation beyond the quoted strike range, and asserting on
    # CHECK_GRID's full +/-1.5 would be testing extrapolation, not recovery.
    #
    # The comparison is in volatility points, not raw total variance. Raw SVI
    # has genuine parameter degeneracy — the recovered vector here differs from
    # the generating one in the third decimal while tracing the same curve — so
    # the meaningful question is whether the published quantity agrees, and the
    # published quantity is a volatility.
    grid = np.linspace(k.min(), k.max(), 400)
    vol_error = np.abs(
        fit.params.implied_vol(grid, T) - GOOD.implied_vol(grid, T)
    ) * 100.0
    assert vol_error.max() < 0.10, f"worst error {vol_error.max():.4f} vol points"

    # Fitted to exact data, so residual vol error should be negligible.
    assert fit.rmse_vol_points < 0.05
    assert fit.butterfly_ok


def test_calibration_is_robust_to_quote_noise():
    k = np.linspace(-0.55, 0.45, 60)
    rng = np.random.default_rng(11)
    w_noisy = GOOD.total_variance(k) * (1.0 + rng.normal(0.0, 0.004, size=k.shape))

    fit = calibrate(k, w_noisy, T)
    assert fit.converged
    # Under half a vol point of RMSE against noisy input.
    assert fit.rmse_vol_points < 0.5

    atm_fitted = float(fit.params.implied_vol(0.0, T))
    atm_true = float(GOOD.implied_vol(0.0, T))
    assert atm_fitted == pytest.approx(atm_true, rel=0.01)


def test_calibration_respects_the_positivity_constraint():
    k = np.linspace(-0.5, 0.5, 40)
    fit = calibrate(k, GOOD.total_variance(k), T)
    assert fit.params.min_total_variance >= -1e-9


def test_too_few_points_returns_unconverged_rather_than_guessing():
    fit = calibrate([0.0, 0.1, -0.1], [0.01, 0.011, 0.012], T)
    assert not fit.converged
    assert np.isnan(fit.params.a)


def test_weighting_protects_the_money_from_biased_wings():
    """Why the fit weights by vega rather than equally.

    The realistic wing failure is *bias*, not noise: wing markets are wide, and
    the mid of a wide market is systematically off. Symmetric random noise
    averages out of a five-parameter global fit, so it does not discriminate
    between weighting schemes — a biased wing does, because it drags the whole
    curve. This test uses the failure mode that actually occurs.
    """
    k = np.linspace(-0.8, 0.6, 70)
    w_true = GOOD.total_variance(k)

    w_dirty = w_true.copy()
    wings = np.abs(k) > 0.45
    w_dirty[wings] *= 1.08  # wing implied variance biased 8% high

    equal = calibrate(k, w_dirty, T)
    vega_like = calibrate(k, w_dirty, T, weights=np.exp(-0.5 * (k / 0.15) ** 2))

    atm_true = float(GOOD.implied_vol(0.0, T))
    err_equal = abs(float(equal.params.implied_vol(0.0, T)) - atm_true)
    err_weighted = abs(float(vega_like.params.implied_vol(0.0, T)) - atm_true)

    assert err_weighted < err_equal


def test_weights_are_actually_applied():
    """Guard against a weights argument that is accepted and then ignored."""
    k = np.linspace(-0.8, 0.6, 70)
    w_dirty = GOOD.total_variance(k)
    w_dirty[np.abs(k) > 0.45] *= 1.08

    equal = calibrate(k, w_dirty, T)
    weighted = calibrate(k, w_dirty, T, weights=np.exp(-0.5 * (k / 0.15) ** 2))
    assert not np.isclose(
        equal.params.implied_vol(0.0, T), weighted.params.implied_vol(0.0, T), rtol=1e-9
    )
