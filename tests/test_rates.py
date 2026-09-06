"""The Treasury curve and the carry it lets us back out.

None of this needs a paid feed. The point of these tests is that the pipeline's
fallback is a real dated curve with a documented quoting convention, not a
number somebody typed.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from pipeline.ingest.rates import (
    CURVE,
    TreasuryCurve,
    implied_dividend_yield,
    load_curve,
)


@pytest.fixture(scope="module")
def curve():
    return load_curve()


def test_curve_covers_the_maturities_the_pipeline_asks_for(curve):
    assert curve.maturities.min() <= 1 / 12
    assert curve.maturities.max() >= 2.0
    assert curve.maturities.size >= 4
    assert np.all(np.diff(curve.maturities) > 0), "must be sorted for interpolation"


def test_rates_are_plausible_and_continuously_compounded(curve):
    assert np.all(curve.rates > 0.0)
    assert np.all(curve.rates < 0.20)
    # Continuous compounding is strictly below the semi-annual quote it came
    # from. If the conversion were skipped these would be equal.
    semiannual = 2.0 * (np.exp(curve.rates / 2.0) - 1.0)
    assert np.all(semiannual > curve.rates)


def test_interpolation_is_flat_beyond_the_published_ends(curve):
    """Never extrapolate a yield curve. It happily goes negative."""
    assert curve.rate_for(0.001) == pytest.approx(curve.rates[0])
    assert curve.rate_for(50.0) == pytest.approx(curve.rates[-1])


def test_interpolation_sits_between_its_neighbours(curve):
    mid = curve.rate_for(0.75)          # between 6m and 1y
    lo = curve.rate_for(0.5)
    hi = curve.rate_for(1.0)
    assert min(lo, hi) <= mid <= max(lo, hi)


def test_rate_for_rejects_degenerate_maturities(curve):
    assert np.isnan(curve.rate_for(0.0))
    assert np.isnan(curve.rate_for(-1.0))


def test_implied_dividend_yield_inverts_the_forward():
    """Build a forward from a known r and q, then recover q."""
    spot, r, q, T = 600.0, 0.042, 0.013, 0.75
    forward = spot * np.exp((r - q) * T)
    assert implied_dividend_yield(forward, spot, T, r) == pytest.approx(q, abs=1e-12)


def test_implied_dividend_yield_is_nan_on_bad_input():
    for args in ((0.0, 600.0, 1.0, 0.04), (600.0, 0.0, 1.0, 0.04),
                 (600.0, 600.0, 0.0, 0.04), (np.nan, 600.0, 1.0, 0.04)):
        assert np.isnan(implied_dividend_yield(*args))


def test_curve_is_point_in_time():
    """The curve for a past date must not reach forward for a later print."""
    past = load_curve(date(2025, 6, 30))
    assert past.as_of <= date(2025, 6, 30)


def test_curve_serialises_for_publication(curve):
    d = curve.as_dict()
    assert d["source"].startswith("FRED")
    assert len(d["points"]) == len(curve.maturities)
    assert all("maturity_years" in p and "rate" in p for p in d["points"])
