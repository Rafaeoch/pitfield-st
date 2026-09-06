"""Realized volatility estimators against a path with a known volatility.

The generator simulates an actual one-minute intraday path, so high and low are
true path extrema and each estimator can be held to the quantity it genuinely
estimates:

  * **Close-to-close** sees the whole daily return, gap included, so it recovers
    the *total* volatility.
  * **Parkinson, Garman-Klass and Rogers-Satchell** use only intraday prices.
    They are structurally blind to the overnight gap and recover the *session*
    volatility. Expecting them to match total volatility is the single most
    common misreading of these estimators, so the tests pin the distinction.
  * **Yang-Zhang** is the one estimator that uses both, and recovers the total.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.compute.realized_vol import (
    HORIZONS,
    all_estimators,
    close_to_close,
    garman_klass,
    parkinson,
    rogers_satchell,
    yang_zhang,
)
from pipeline.ingest.synthetic import synthetic_ohlcv

TRUE_VOL = 0.145
OVERNIGHT_SHARE = 0.20
# What an intraday-only estimator can actually see.
SESSION_VOL = TRUE_VOL * np.sqrt(1.0 - OVERNIGHT_SHARE)


@pytest.fixture(scope="module")
def bars():
    return synthetic_ohlcv(
        n_days=6000, annual_vol=TRUE_VOL, overnight_share=OVERNIGHT_SHARE, seed=99
    )


def test_close_to_close_recovers_total_volatility(bars):
    assert close_to_close(bars["close"]) == pytest.approx(TRUE_VOL, rel=0.05)


def test_yang_zhang_recovers_total_volatility(bars):
    """YZ is the only estimator here that uses both the gap and the session."""
    assert yang_zhang(**bars) == pytest.approx(TRUE_VOL, rel=0.10)


@pytest.mark.parametrize("estimator", [garman_klass, rogers_satchell])
def test_range_estimators_recover_session_volatility(bars, estimator):
    assert estimator(**bars) == pytest.approx(SESSION_VOL, rel=0.06)


def test_parkinson_recovers_session_volatility(bars):
    assert parkinson(bars["high"], bars["low"]) == pytest.approx(SESSION_VOL, rel=0.06)


def test_range_estimators_sit_below_close_to_close(bars):
    """Because they miss the gap, they must come in under CC on gappy data."""
    assert parkinson(bars["high"], bars["low"]) < close_to_close(bars["close"])
    assert rogers_satchell(**bars) < close_to_close(bars["close"])


def test_rogers_satchell_is_drift_independent():
    """RS must be unmoved by a large drift; Parkinson and GK are not."""
    flat = synthetic_ohlcv(n_days=4000, annual_vol=0.20, drift=0.0, seed=5)
    trending = synthetic_ohlcv(n_days=4000, annual_vol=0.20, drift=0.60, seed=5)

    rs_flat = rogers_satchell(**flat)
    rs_trend = rogers_satchell(**trending)
    assert rs_flat == pytest.approx(rs_trend, rel=0.05)


def test_all_horizons_present(bars):
    out = all_estimators(**bars)
    for n in HORIZONS:
        for name in ("cc", "park", "gk", "rs", "yz"):
            assert f"rv_{name}_{n}" in out
        assert f"rv_park_cc_ratio_{n}" in out


def test_short_history_returns_nan_not_a_mislabelled_estimate():
    """A 252-day estimate must never be computed from 30 bars."""
    short = synthetic_ohlcv(n_days=30, seed=1)
    out = all_estimators(**short)
    assert np.isnan(out["rv_cc_252"])
    assert np.isfinite(out["rv_cc_5"])


def test_parkinson_close_ratio_is_published(bars):
    out = all_estimators(**bars)
    ratio = out["rv_park_cc_ratio_252"]
    assert np.isfinite(ratio)
    assert 0.3 < ratio < 3.0


def test_estimators_return_nan_on_degenerate_input():
    assert np.isnan(close_to_close(np.array([100.0])))
    assert np.isnan(yang_zhang(*(np.array([100.0]),) * 4))
