"""Synthetic option chains, for development and for round-trip testing.

WHAT THIS IS NOT
----------------
This is not market data. Nothing produced here may ever be published as though
it were observed. Every row carries ``source="SYNTHETIC"`` and every artefact
built from it carries ``synthetic: true``, which the site renders as a banner.
The QC suite refuses to sign off a day whose rows are synthetic.

WHY IT EXISTS
-------------
Two reasons, both real:

1. The pipeline has to be runnable end to end by anyone who clones the repo,
   including someone with no options data licence. ``make demo`` produces a
   complete day's output from this generator.

2. It makes the calibration *falsifiable*. Chains are priced from a known
   forward, a known discount rate and known SVI parameters, so the tests can
   assert that the pipeline recovers the inputs it was given. A calibrator that
   cannot recover parameters it was handed is broken, and round-tripping is the
   only way to find that out without a reference implementation.

The generator deliberately introduces the things that break naive code: a
dividend yield so spot is not the forward, wing spreads that widen, penny quotes
that round to a tick, and a scattering of zero-bid contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np

from ..compute.black76 import price
from ..compute.svi import SVIParams

SOURCE_TAG = "SYNTHETIC"
DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class SyntheticTruth:
    """The parameters a chain was generated from, for tests to assert against."""

    spot: float
    rate: float
    dividend_yield: float
    forwards: dict[float, float]          # T -> F
    svi: dict[float, SVIParams]           # T -> params


def _default_svi(T: float, atm_vol: float) -> SVIParams:
    """A plausible equity-index smile: negative skew, flattening with maturity.

    The whole curve is scaled to the ATM total variance ``w0 = atm_vol^2 T``
    rather than having its five parameters set independently. Writing

        w(0) = a + b [ rho (-m) + sqrt(m^2 + sigma^2) ] = w0

    and choosing to source a fixed fraction ``c`` of w0 from the b-term gives

        b = c w0 / [ rho (-m) + sqrt(m^2 + sigma^2) ]
        a = (1 - c) w0

    Two things fall out of this, both of which the naive version got wrong:

    * ``a > 0`` for any ``c < 1``, so ``w_min = a + b sigma sqrt(1 - rho^2)`` is
      positive and the surface cannot produce negative total variance. Setting
      ``b`` and ``sigma`` independently of maturity drives ``a`` negative at
      short expiries, where ``w0`` is tiny — the generated 7-day smile then has
      negative implied variance in the wings and infinite Durrleman violation.
    * The smile scales with maturity instead of keeping a fixed absolute width,
      so ATM volatility is flat across expiries and ATM total variance is linear
      in T, satisfying the calendar condition by construction.
    """
    rho = -0.68               # equity skew: puts bid over calls
    m = 0.02
    sigma = 0.10 + 0.35 * np.sqrt(T)   # wings widen with maturity
    c = 0.55                  # share of ATM total variance from the b-term

    w0 = atm_vol**2 * T
    s0 = np.sqrt(m**2 + sigma**2)
    b = c * w0 / (rho * (-m) + s0)
    a = (1.0 - c) * w0

    return SVIParams(a=float(a), b=float(b), rho=rho, m=m, sigma=float(sigma))


def _tick_size(px: float) -> float:
    """US options: nickels above $3, pennies below."""
    return 0.05 if px >= 3.0 else 0.01


def _round_to_tick(px: np.ndarray) -> np.ndarray:
    ticks = np.where(px >= 3.0, 0.05, 0.01)
    return np.round(px / ticks) * ticks


def synthetic_chain(
    underlying: str,
    asof: date,
    *,
    spot: float = 585.0,
    rate: float = 0.0435,
    dividend_yield: float = 0.0130,
    # A realistic listed ladder: weeklies out to roughly two months, then
    # monthlies and quarterlies. A chain with only a handful of expiries is not
    # what an index actually lists, and it makes the term structure look far
    # coarser than it is.
    expiry_days: tuple[int, ...] = (
        7, 14, 21, 28, 35, 49, 63, 91, 119, 154, 182, 273, 365,
    ),
    atm_vol: float = 0.152,
    strikes_per_expiry: int = 81,
    seed: int | None = None,
) -> tuple[list[dict], SyntheticTruth]:
    """Generate one day's chain for one underlying.

    Returns ``(rows, truth)`` where rows match the §4.3 schema and ``truth``
    records the parameters used, so a test can check the pipeline recovers them.
    """
    rng = np.random.default_rng(seed)
    snapshot_ts = datetime.combine(asof, datetime.min.time(), tzinfo=timezone.utc).replace(
        hour=20, minute=20
    )

    rows: list[dict] = []
    forwards: dict[float, float] = {}
    svi_by_t: dict[float, SVIParams] = {}

    for days in expiry_days:
        T = days / DAYS_PER_YEAR
        forward = spot * np.exp((rate - dividend_yield) * T)
        params = _default_svi(T, atm_vol)
        forwards[T] = float(forward)
        svi_by_t[T] = params

        # Strike grid: standard increments, centred on spot rather than the
        # forward, exactly as an exchange lists them.
        increment = 5.0 if spot > 200 else 1.0
        centre = np.round(spot / increment) * increment
        half = strikes_per_expiry // 2
        strikes = centre + increment * np.arange(-half, half + 1)
        strikes = strikes[strikes > 0]

        k = np.log(strikes / forward)
        sigma = params.implied_vol(k, T)

        for right in ("C", "P"):
            theo = price(forward, strikes, T, sigma, rate, right)
            theo = np.maximum(theo, 0.0)

            # Spread model: a floor in absolute terms, widening in the wings and
            # for cheap contracts. This is what makes wing quotes uninformative
            # and is why the fit weights by vega rather than equally.
            rel_wing = np.abs(k)
            half_spread = np.maximum(
                0.01 + 0.006 * theo + 0.05 * rel_wing**2,
                _tick_size(1.0),
            )
            bid = _round_to_tick(np.maximum(theo - half_spread, 0.0))
            ask = _round_to_tick(theo + half_spread)
            # A rounded bid can land on or above the ask for near-worthless
            # contracts; keep the book sane by construction.
            ask = np.where(ask <= bid, bid + 0.01, ask)

            # Deep OTM contracts go no-bid, as they do in a real chain.
            no_bid = theo < 0.02
            bid = np.where(no_bid, 0.0, bid)

            # Open interest and volume peak near the money and on round strikes.
            atm_weight = np.exp(-0.5 * (k / 0.10) ** 2)
            round_bonus = np.where(np.mod(strikes, 25.0) == 0, 2.2, 1.0)
            oi = rng.poisson(np.maximum(4000 * atm_weight * round_bonus, 1.0))
            volume = rng.poisson(np.maximum(0.35 * oi, 0.5))

            for i, strike in enumerate(strikes):
                rows.append(
                    {
                        "snapshot_ts": snapshot_ts,
                        "quote_ts": snapshot_ts - timedelta(seconds=int(rng.integers(1, 90))),
                        "underlying": underlying,
                        "expiry": asof + timedelta(days=int(days)),
                        "strike": float(strike),
                        "right": right,
                        "bid": float(bid[i]),
                        "ask": float(ask[i]),
                        "last": float(np.round(theo[i], 2)),
                        "volume": int(volume[i]),
                        "open_interest": int(oi[i]),
                        "multiplier": 100,
                        "is_adjusted": False,
                        "spot": float(spot),
                        "source": SOURCE_TAG,
                        "ingest_version": "synthetic-1",
                    }
                )

    truth = SyntheticTruth(
        spot=spot,
        rate=rate,
        dividend_yield=dividend_yield,
        forwards=forwards,
        svi=svi_by_t,
    )
    return rows, truth


def synthetic_ohlcv(
    n_days: int = 300,
    *,
    start_price: float = 585.0,
    annual_vol: float = 0.145,
    drift: float = 0.06,
    overnight_share: float = 0.20,
    intraday_steps: int = 390,
    seed: int | None = 7,
) -> dict[str, np.ndarray]:
    """A daily OHLCV series simulated from an actual intraday path.

    The high and low are the true extremes of a simulated one-minute path, not a
    closed-form approximation of the range. This matters: the range estimators
    (Parkinson, Garman-Klass, Rogers-Satchell) are only meaningful against a
    high and low that a diffusion actually produced, and testing them against an
    approximated range tests the approximation instead of the estimator.

    Total variance is split between the overnight gap and the session, which is
    exactly the decomposition Yang-Zhang inverts. The consequence, and what the
    tests assert:

      * close-to-close recovers the **total** volatility;
      * the range estimators recover the **session** volatility, because they
        see only intraday data and are blind to the gap;
      * Yang-Zhang recovers the total, because it is the one estimator that
        uses both.
    """
    rng = np.random.default_rng(seed)
    dt = 1.0 / 252.0

    sigma_on = annual_vol * np.sqrt(overnight_share)
    sigma_session = annual_vol * np.sqrt(1.0 - overnight_share)

    # Overnight gap: one jump per day, applied to the previous close.
    gaps = rng.normal(-0.5 * sigma_on**2 * dt, sigma_on * np.sqrt(dt), n_days)

    # Session: a discretised GBM path, so high/low are genuine path extrema.
    step_dt = dt / intraday_steps
    steps = rng.normal(
        (drift - 0.5 * sigma_session**2) * step_dt,
        sigma_session * np.sqrt(step_dt),
        (n_days, intraday_steps),
    )
    path = np.cumsum(steps, axis=1)  # log price relative to that day's open
    session_total = path[:, -1]

    log_close = np.log(start_price) + np.cumsum(gaps + session_total)
    log_open = log_close - session_total
    # The open itself is part of the session, hence the clip against zero.
    log_high = log_open + np.maximum(path.max(axis=1), 0.0)
    log_low = log_open + np.minimum(path.min(axis=1), 0.0)

    return {
        "open": np.exp(log_open),
        "high": np.exp(log_high),
        "low": np.exp(log_low),
        "close": np.exp(log_close),
    }
