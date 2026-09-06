"""Black-76: parity, inversion round-trip, and every greek against a numerical derivative.

The greeks are checked against finite differences of the pricer rather than
against a second closed form. A closed form copied next to the one under test
proves only that both were copied from the same place; differentiating the
actual pricer proves the analytic expression is the derivative of the thing we
ship.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.compute.black76 import (
    THETA_BUMP_YEARS,
    d1_d2,
    greeks,
    implied_vol,
    price,
)

F, K, T, SIGMA, R = 600.0, 610.0, 0.25, 0.18, 0.045


def _num_deriv(fn, x, h):
    """Central difference."""
    return (fn(x + h) - fn(x - h)) / (2.0 * h)


def _num_second(fn, x, h):
    return (fn(x + h) - 2.0 * fn(x) + fn(x - h)) / (h * h)


def test_put_call_parity():
    """C - P = e^{-rT}(F - K). This is the identity the whole forward extraction rests on."""
    c = float(price(F, K, T, SIGMA, R, "C"))
    p = float(price(F, K, T, SIGMA, R, "P"))
    assert c - p == pytest.approx(np.exp(-R * T) * (F - K), abs=1e-10)


def test_price_is_above_discounted_intrinsic():
    deep_itm = float(price(F, 400.0, T, SIGMA, R, "C"))
    assert deep_itm > np.exp(-R * T) * (F - 400.0)


def test_zero_vol_collapses_to_intrinsic():
    """Degenerate inputs must produce intrinsic value, not NaN."""
    c = float(price(F, K, T, 0.0, R, "C"))
    assert c == pytest.approx(np.exp(-R * T) * max(F - K, 0.0), abs=1e-12)


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize("strike", [450.0, 550.0, 600.0, 650.0, 780.0])
def test_implied_vol_round_trips(right, strike):
    """Price at a known vol, invert, recover the vol."""
    px = float(price(F, strike, T, SIGMA, R, right))
    recovered = implied_vol(px, F, strike, T, R, right)
    assert recovered == pytest.approx(SIGMA, abs=1e-8)


def test_implied_vol_returns_nan_outside_no_arbitrage_bounds():
    """A price below intrinsic has no implied vol; it must not be coerced to one."""
    below_intrinsic = np.exp(-R * T) * (F - 400.0) * 0.5
    assert np.isnan(implied_vol(below_intrinsic, F, 400.0, T, R, "C"))
    assert np.isnan(implied_vol(-1.0, F, K, T, R, "C"))


@pytest.mark.parametrize("right", ["C", "P"])
def test_delta_matches_dV_dF(right):
    g = greeks(F, K, T, SIGMA, R, right)
    numeric = _num_deriv(lambda f: float(price(f, K, T, SIGMA, R, right)), F, 1e-4)
    assert float(g.delta) == pytest.approx(numeric, rel=1e-6)


@pytest.mark.parametrize("right", ["C", "P"])
def test_gamma_matches_d2V_dF2(right):
    g = greeks(F, K, T, SIGMA, R, right)
    numeric = _num_second(lambda f: float(price(f, K, T, SIGMA, R, right)), F, 1e-2)
    assert float(g.gamma) == pytest.approx(numeric, rel=1e-5)


@pytest.mark.parametrize("right", ["C", "P"])
def test_gamma_is_identical_for_calls_and_puts(right):
    """Parity is linear in F, so its second derivative vanishes."""
    call_g = float(greeks(F, K, T, SIGMA, R, "C").gamma)
    put_g = float(greeks(F, K, T, SIGMA, R, "P").gamma)
    assert call_g == pytest.approx(put_g, rel=1e-12)


def test_vega_is_per_volatility_point():
    """Reported vega must be dV/dsigma scaled to a 1-point (0.01) move."""
    g = greeks(F, K, T, SIGMA, R, "C")
    raw = _num_deriv(lambda s: float(price(F, K, T, s, R, "C")), SIGMA, 1e-6)
    assert float(g.vega) == pytest.approx(raw / 100.0, rel=1e-6)


def test_vanna_is_dVega_dF():
    """Vanna = d^2V/dF dsigma, compared against the raw (un-scaled) vega."""
    g = greeks(F, K, T, SIGMA, R, "C")

    def raw_vega(f):
        return _num_deriv(lambda s: float(price(f, K, T, s, R, "C")), SIGMA, 1e-6)

    assert float(g.vanna) == pytest.approx(_num_deriv(raw_vega, F, 1e-3), rel=1e-4)


def test_volga_is_dVega_dsigma():
    g = greeks(F, K, T, SIGMA, R, "C")
    numeric = _num_second(lambda s: float(price(F, K, T, s, R, "C")), SIGMA, 1e-4)
    assert float(g.volga) == pytest.approx(numeric, rel=1e-4)


@pytest.mark.parametrize("right", ["C", "P"])
def test_theta_is_one_calendar_day_of_decay(right):
    """Theta must equal the repriced value one calendar day later, by construction."""
    g = greeks(F, K, T, SIGMA, R, right)
    expected = float(price(F, K, T - THETA_BUMP_YEARS, SIGMA, R, right)) - float(
        price(F, K, T, SIGMA, R, right)
    )
    assert float(g.theta) == pytest.approx(expected, abs=1e-12)
    assert THETA_BUMP_YEARS == pytest.approx(1.0 / 365.0)


def test_atm_theta_is_negative():
    """Sanity: an at-the-money option loses value as time passes."""
    assert float(greeks(F, F, T, SIGMA, R, "C").theta) < 0.0


def test_d1_d2_relationship():
    d1, d2 = d1_d2(F, K, T, SIGMA)
    assert float(d1 - d2) == pytest.approx(SIGMA * np.sqrt(T), abs=1e-12)


def test_vectorises_over_a_chain():
    strikes = np.array([500.0, 550.0, 600.0, 650.0, 700.0])
    rights = np.array(["C", "C", "C", "P", "P"])
    px = price(F, strikes, T, SIGMA, R, rights)
    assert px.shape == strikes.shape
    assert np.all(np.isfinite(px))
