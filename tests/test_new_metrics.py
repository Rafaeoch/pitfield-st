"""Momentum, model-free variance and the new options structure metrics.

Each is checked against something known independently: a variance swap rate
against a flat-smile analytic, momentum against a hand-built price path,
forward volatility against the additivity of total variance.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.compute.aggregates import max_pain, open_interest_concentration
from pipeline.compute.black76 import price
from pipeline.compute.momentum import (
    all_metrics,
    distance_from_moving_average,
    downside_deviation,
    drawdown,
    momentum_12_1,
    realized_moments,
    trailing_return,
)
from pipeline.compute.surface_metrics import forward_volatility, implied_move
from pipeline.compute.variance import interpolate_30_day, model_free_variance


# --- model-free implied variance -----------------------------------------

def test_recovers_a_flat_smile():
    """With constant volatility the strip must return that volatility.

    Built by pricing a dense chain at one vol and integrating it back. If the
    dK weighting or the K0 correction were wrong this would miss.
    """
    F, r, T, sigma = 600.0, 0.04, 0.25, 0.20
    strikes = np.arange(300.0, 900.0, 2.5)
    calls = price(F, strikes, T, sigma, r, "C")
    puts = price(F, strikes, T, sigma, r, "P")

    got = model_free_variance(strikes, calls, puts, F, r, T)
    assert got.volatility == pytest.approx(sigma, rel=0.02)
    assert got.n_strikes > 100


def test_a_skewed_smile_prices_above_its_atm_volatility():
    """Model-free variance integrates the whole smile, so a skew lifts it."""
    F, r, T = 600.0, 0.04, 0.25
    strikes = np.arange(350.0, 850.0, 2.5)
    k = np.log(strikes / F)
    smile = 0.20 - 0.15 * k          # negative skew: puts bid over calls
    calls = price(F, strikes, T, smile, r, "C")
    puts = price(F, strikes, T, smile, r, "P")

    got = model_free_variance(strikes, calls, puts, F, r, T)
    assert got.volatility > 0.20


def test_returns_empty_on_a_chain_too_thin_to_integrate():
    empty = model_free_variance(np.array([600.0]), np.array([1.0]),
                                np.array([1.0]), 600.0, 0.04, 0.25)
    assert np.isnan(empty.volatility)
    assert empty.n_strikes == 0


def test_handles_a_non_uniform_strike_grid():
    """Real chains mix 1-point and 5-point increments; dK must follow."""
    F, r, T, sigma = 600.0, 0.04, 0.25, 0.20
    strikes = np.concatenate([np.arange(400.0, 560.0, 5.0),
                              np.arange(560.0, 640.0, 1.0),
                              np.arange(640.0, 800.0, 5.0)])
    calls = price(F, strikes, T, sigma, r, "C")
    puts = price(F, strikes, T, sigma, r, "P")
    assert model_free_variance(strikes, calls, puts, F, r, T).volatility == pytest.approx(sigma, rel=0.03)


def test_constant_maturity_interpolates_in_total_variance():
    t1, t2 = 30 / 365, 90 / 365
    per_expiry = [(t1, 0.20**2 * t1), (t2, 0.22**2 * t2)]
    assert interpolate_30_day(per_expiry, 30.0) == pytest.approx(0.20, abs=1e-9)

    # Halfway in total variance, not halfway in volatility.
    mid = interpolate_30_day(per_expiry, 60.0)
    assert 0.20 < mid < 0.22

    # Beyond the listed range we refuse rather than extrapolate.
    assert np.isnan(interpolate_30_day(per_expiry, 400.0))
    assert np.isnan(interpolate_30_day(per_expiry, 1.0))


def test_a_listed_expiry_at_exactly_the_target_is_not_rejected():
    """30/365 is 0.08219178; an expiry recorded as 0.0822 is the same expiry."""
    per_expiry = [(0.0822, 0.20**2 * 0.0822), (0.2466, 0.22**2 * 0.2466)]
    assert np.isfinite(interpolate_30_day(per_expiry, 30.0))


# --- forward volatility and implied move ---------------------------------

def test_forward_volatility_is_flat_when_the_term_structure_is():
    assert forward_volatility(0.25, 0.20, 0.50, 0.20) == pytest.approx(0.20)


def test_forward_volatility_exceeds_both_when_the_curve_rises():
    fwd = forward_volatility(0.25, 0.18, 0.50, 0.22)
    assert fwd > 0.22


def test_forward_volatility_is_nan_on_a_calendar_arbitrage():
    """Total variance falling with maturity has no real forward vol."""
    assert np.isnan(forward_volatility(0.25, 0.30, 0.50, 0.10))


def test_implied_move_is_one_sigma():
    assert implied_move(0.20, 0.25) == pytest.approx(0.10)
    assert np.isnan(implied_move(0.20, 0.0))


# --- momentum -------------------------------------------------------------

def test_trailing_return_on_a_known_path():
    close = np.array([100.0] * 300 + [110.0])
    assert trailing_return(close, 21) == pytest.approx(0.10)


def test_momentum_12_1_skips_the_most_recent_month():
    """The last month must not affect the answer. That is the construction."""
    base = np.linspace(100.0, 150.0, 300)
    a = base.copy()
    b = base.copy()
    b[-21:] *= 1.5           # a violent final month
    assert momentum_12_1(a) == pytest.approx(momentum_12_1(b))


def test_drawdown_measures_from_the_running_peak():
    close = np.array([100.0, 120.0, 90.0, 95.0])
    current, deepest = drawdown(close)
    assert current == pytest.approx(95 / 120 - 1)
    assert deepest == pytest.approx(90 / 120 - 1)


def test_distance_from_moving_average():
    close = np.array([100.0] * 199 + [110.0])
    assert distance_from_moving_average(close, 200) == pytest.approx(110 / 100.05 - 1, rel=1e-3)


def test_realized_moments_detect_left_skew():
    rng = np.random.default_rng(4)
    r = rng.normal(0, 0.01, 3000)
    r[::200] -= 0.06                     # occasional crashes
    close = 100 * np.exp(np.cumsum(r))
    m = realized_moments(close, 2000)
    assert m["realized_skew"] < -0.3
    assert m["realized_excess_kurtosis"] > 1.0


def test_downside_deviation_is_below_total_volatility():
    rng = np.random.default_rng(5)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, 900)))
    total = float(np.std(np.diff(np.log(close)), ddof=1) * np.sqrt(252))
    assert 0 < downside_deviation(close) < total * 1.1


def test_all_metrics_returns_the_full_set():
    rng = np.random.default_rng(6)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 800)))
    m = all_metrics(close)
    for key in ("return_1m", "return_12m", "momentum_12_1", "dist_from_ma200",
                "drawdown_current", "realized_skew", "downside_deviation_252"):
        assert key in m and np.isfinite(m[key]), key


# --- chain structure ------------------------------------------------------

def test_max_pain_sits_where_open_interest_is_heaviest():
    """All open interest at one strike puts max pain at that strike."""
    strikes = np.array([90.0, 100.0, 110.0, 90.0, 100.0, 110.0])
    right = np.array(["C", "C", "C", "P", "P", "P"])
    oi = np.array([0.0, 1000.0, 0.0, 0.0, 1000.0, 0.0])
    assert max_pain(strikes, oi, right) == pytest.approx(100.0)


def test_max_pain_is_nan_without_enough_open_interest():
    assert np.isnan(max_pain(np.array([100.0]), np.array([np.nan]), np.array(["C"])))


def test_open_interest_concentration():
    strikes = np.arange(100.0, 120.0)
    spread = open_interest_concentration(strikes, np.full(20, 100.0))
    concentrated = open_interest_concentration(strikes, np.array([10000.0] + [1.0] * 19))
    assert concentrated["oi_herfindahl"] > spread["oi_herfindahl"]
    assert concentrated["oi_top5_share"] > spread["oi_top5_share"]
    assert spread["oi_herfindahl"] == pytest.approx(1 / 20, rel=1e-6)
