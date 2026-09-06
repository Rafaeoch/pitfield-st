"""Forward extraction must recover the forward and rate it was generated from.

The synthetic chain is built with a dividend yield, so the forward is *not* spot.
If the parity regression only recovered spot, these tests would fail — which is
the point: the whole reason for extracting rather than assuming is that spot and
the forward differ by dividends and borrow.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from pipeline.compute.forward import extract_forward
from pipeline.ingest.synthetic import synthetic_chain

ASOF = date(2026, 3, 12)


def _chain_by_expiry(rows):
    """Group synthetic rows into {T: (strikes, call_mid, put_mid)}."""
    by_expiry: dict = {}
    for row in rows:
        T = (row["expiry"] - ASOF).days / 365.0
        by_expiry.setdefault(T, {}).setdefault(row["strike"], {})[row["right"]] = (
            (row["bid"] + row["ask"]) / 2.0
        )

    out = {}
    for T, strikes in by_expiry.items():
        ks = sorted(strikes)
        calls = np.array([strikes[k].get("C", np.nan) for k in ks])
        puts = np.array([strikes[k].get("P", np.nan) for k in ks])
        out[T] = (np.array(ks), calls, puts)
    return out


@pytest.fixture(scope="module")
def generated():
    rows, truth = synthetic_chain("SPY", ASOF, seed=42)
    return _chain_by_expiry(rows), truth


def test_recovers_the_forward_at_every_maturity(generated):
    """The forward is the number this module exists to produce. It must be exact."""
    chains, truth = generated
    for T, (strikes, calls, puts) in chains.items():
        fit = extract_forward(strikes, calls, puts, T, spot=truth.spot)
        assert fit.accepted, f"expiry T={T:.3f} rejected: {fit.reason}"
        assert fit.forward == pytest.approx(truth.forwards[T], rel=1e-4)


def test_recovers_the_rate_beyond_a_month(generated):
    """Rate precision is usable once 1/T amplification is no longer dominant."""
    chains, truth = generated
    for T, (strikes, calls, puts) in chains.items():
        if T < 25 / 365:
            continue
        fit = extract_forward(strikes, calls, puts, T, spot=truth.spot)
        assert fit.rate == pytest.approx(truth.rate, abs=1e-3)


def test_short_dated_rate_is_noisier_than_long_dated(generated):
    """Pins the 1/T amplification documented in the module.

    r = -ln(-beta)/T, so a fixed slope error from tick rounding scales as 1/T.
    This is a property of the estimator, not a defect, and the site says so
    rather than quietly publishing a one-week rate as though it were as good as
    the one-year rate.
    """
    chains, truth = generated
    errors = {}
    for T, (strikes, calls, puts) in chains.items():
        fit = extract_forward(strikes, calls, puts, T, spot=truth.spot)
        errors[T] = abs(fit.rate - truth.rate)

    shortest, longest = min(errors), max(errors)
    assert errors[shortest] > errors[longest]
    # And the long end really is tight, not merely relatively better.
    assert errors[longest] < 1e-4


def test_forward_differs_from_spot(generated):
    """Guards against a regression that returns spot and passes by luck."""
    chains, truth = generated
    longest = max(chains)
    fit = extract_forward(*chains[longest], longest, spot=truth.spot)
    assert abs(fit.forward - truth.spot) > 1.0


def test_r_squared_is_essentially_one_on_clean_quotes(generated):
    chains, truth = generated
    for T, (strikes, calls, puts) in chains.items():
        fit = extract_forward(strikes, calls, puts, T, spot=truth.spot)
        assert fit.r2 >= 0.99


def test_rejects_expiry_when_quotes_are_corrupted(generated):
    """Scrambled parity must fail the R-squared gate rather than fit noise."""
    chains, truth = generated
    T = sorted(chains)[3]
    strikes, calls, puts = chains[T]

    rng = np.random.default_rng(0)
    corrupted = calls + rng.normal(0.0, 12.0, size=calls.shape)

    fit = extract_forward(strikes, corrupted, puts, T, spot=truth.spot)
    assert not fit.accepted
    assert "r2" in fit.reason or "forward" in fit.reason


def test_rejects_too_few_strikes():
    fit = extract_forward([100.0, 105.0], [5.0, 2.0], [1.0, 3.0], 0.25, spot=100.0)
    assert not fit.accepted
    assert fit.reason == "too_few_two_sided_strikes"


def test_rejects_non_future_expiry():
    fit = extract_forward([100.0] * 6, [1.0] * 6, [1.0] * 6, 0.0)
    assert not fit.accepted
    assert fit.reason == "expiry_not_in_future"


def test_ignores_one_sided_strikes(generated):
    """A strike quoted on only one leg carries no parity information."""
    chains, truth = generated
    T = sorted(chains)[4]
    strikes, calls, puts = chains[T]

    holed = puts.copy()
    holed[::3] = np.nan

    fit = extract_forward(strikes, calls, holed, T, spot=truth.spot)
    assert fit.accepted
    assert fit.forward == pytest.approx(truth.forwards[T], rel=5e-4)
