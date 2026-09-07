"""Run one trading day end to end: chain in, surface and metrics out.

This is the spine. Every number the site publishes comes through here, in this
order:

    chain -> forward (parity) -> implied vol (Black-76) -> SVI -> arbitrage
          -> greeks -> aggregates -> metrics -> quality -> export

The order is not arbitrary. Each step consumes the previous step's output, and
nothing downstream is allowed to reach around a rejected step: an expiry whose
parity regression fails is not fitted, and an expiry that is not fitted
contributes to no metric. Rejections are counted and published rather than
quietly skipped.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .compute import momentum, realized_vol, surface_metrics
from .compute.variance import interpolate_30_day, model_free_variance
from .compute.aggregates import (
    DEFAULT_SIGN_CONVENTION,
    chain_aggregates,
    max_pain,
    open_interest_concentration,
)
from .compute.black76 import greeks, implied_vol
from .compute.forward import extract_forward
from .compute.svi import CHECK_GRID, SVIParams, calibrate, check_calendar
from .ingest.rates import implied_dividend_yield, load_curve
from .ingest.synthetic import synthetic_chain, synthetic_ohlcv
from .quality.checks import run_checks

DAYS_PER_YEAR = 365.0

# Contracts outside this log-moneyness band are dropped before calibration: the
# quotes are a tick wide on a near-worthless contract and carry no information
# about the smile.
MAX_ABS_K = 1.5

# Grid the published surface is sampled on.
SURFACE_K_GRID = np.linspace(-0.45, 0.35, 49)

PIPELINE_VERSION = "0.1.0"


@dataclass
class ExpiryResult:
    days: int
    T: float
    forward: float
    rate: float
    parity_r2: float
    accepted: bool
    reason: str
    params: SVIParams | None = None
    rmse_vol_points: float = float("nan")
    butterfly_ok: bool = False
    min_g: float = float("nan")
    n_used: int = 0
    n_dropped: int = 0
    market_k: list[float] | None = None
    market_iv: list[float] | None = None
    # Log-moneyness range where this expiry actually had quotable contracts.
    # Outside it the fit is extrapolating and we neither publish nor check.
    k_min: float = float("nan")
    k_max: float = float("nan")
    rate_source: str = "parity"


def _mid(bid: float, ask: float) -> float:
    """Mid of a two-sided market; NaN if either side is missing."""
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0 or ask < bid:
        return float("nan")
    return (bid + ask) / 2.0


def _group_by_expiry(rows: list[dict], asof: date) -> dict:
    """{days_to_expiry: {strike: {"C": row, "P": row}}}"""
    grouped: dict = {}
    for row in rows:
        days = (row["expiry"] - asof).days
        grouped.setdefault(days, {}).setdefault(row["strike"], {})[row["right"]] = row
    return grouped


# Below this maturity the parity rate is not usable. r = -ln(-beta)/T divides
# by T, so at one day the amplification of a tick-rounding slope error is about
# 365x, and real chains duly produce rates of minus fifty percent. The forward
# from the same regression is unaffected and stays exact.
MIN_T_FOR_RATE = 30.0 / DAYS_PER_YEAR

# A plausible band for a USD discount rate. Outside it, the extraction has
# failed rather than discovered something.
#
# This matters because parity rate extraction assumes the quotes actually
# satisfy parity. Derived feeds do not always: on Alpaca's indicative feed the
# call-minus-put line is systematically steeper than -1, which yields discount
# factors above one and rates around minus six percent, while the *forward*
# from the same regression stays correct — a forward is a ratio of the
# intercept to the slope, so a scale error in the slope largely cancels.
#
# So we keep the extracted forward, test the extracted rate, and fall back to a
# stated assumption when the test fails. Never silently: the fallback is
# recorded per expiry and surfaced in the day's quality report.
RATE_SANITY_BAND = (-0.01, 0.15)


def process_expiry(
    days: int, by_strike: dict, asof: date, spot: float,
    seed_params: SVIParams | None, rate_override: float | None = None,
    force_override: bool = False,
) -> ExpiryResult:
    """Forward extraction, IV inversion and SVI calibration for one expiry.

    ``rate_override`` supplies a discount rate for expiries too short to
    extract one from. The forward always comes from this expiry's own parity
    regression; only the rate is borrowed, and only when 1/T amplification has
    destroyed the local estimate. The discount curve is nearly flat across a
    year, so a cross-sectional rate is a far better estimate of this expiry's
    true rate than this expiry's own noise is.
    """
    T = days / DAYS_PER_YEAR
    strikes = np.array(sorted(by_strike), dtype=float)

    call_mid = np.array(
        [_mid(by_strike[k].get("C", {}).get("bid"), by_strike[k].get("C", {}).get("ask"))
         for k in strikes]
    )
    put_mid = np.array(
        [_mid(by_strike[k].get("P", {}).get("bid"), by_strike[k].get("P", {}).get("ask"))
         for k in strikes]
    )

    fit = extract_forward(strikes, call_mid, put_mid, T, spot=spot)
    if not fit.accepted:
        return ExpiryResult(
            days, T, fit.forward, fit.rate, fit.r2, False, fit.reason
        )

    forward = fit.forward
    rate = fit.rate
    rate_source = "parity"
    if rate_override is not None and (T < MIN_T_FOR_RATE or force_override):
        rate = rate_override
        rate_source = "treasury_curve" if force_override else "cross_sectional"
    log_k = np.log(strikes / forward)

    # Out-of-the-money side only: the OTM quote is the informative one, since the
    # ITM leg's value is dominated by intrinsic and its implied vol is noise
    # amplified by a small vega.
    sel_k, sel_strike, sel_right, sel_mid = [], [], [], []
    n_dropped = 0
    n_oi_known = 0
    for i, strike in enumerate(strikes):
        k = float(log_k[i])
        right = "C" if k >= 0 else "P"
        contract = by_strike[strike].get(right)
        if contract is None:
            continue

        # Open interest filters the chain only when we actually know it.
        # `.get(key, 0)` is wrong here: the key is present with value None on
        # feeds that do not carry open interest, so the default never fires and
        # the comparison raises. More importantly, unknown is not zero — a
        # contract whose open interest we cannot see is not evidence of a
        # contract nobody holds, and dropping it would silently discard most of
        # the chain on any feed without that field.
        oi = contract.get("open_interest")
        oi_known = oi is not None
        if oi_known:
            n_oi_known += 1

        if (
            abs(k) > MAX_ABS_K
            or contract["bid"] <= 0.0
            or contract.get("is_adjusted")
            or contract["bid"] > contract["ask"]
            or (oi_known and oi <= 0)
        ):
            n_dropped += 1
            continue

        sel_k.append(k)
        sel_strike.append(strike)
        sel_right.append(right)
        sel_mid.append(_mid(contract["bid"], contract["ask"]))

    if not sel_k:
        return ExpiryResult(
            days, T, forward, rate, fit.r2, False, "no_quotable_contracts",
            n_dropped=n_dropped,
        )

    # One vectorised inversion for the whole expiry rather than a call per
    # contract: same Brent solve, a fraction of the interpreter overhead.
    sel_k = np.array(sel_k)
    sel_strike = np.array(sel_strike)
    sel_right = np.array(sel_right)
    iv_all = implied_vol(np.array(sel_mid), forward, sel_strike, T, rate, sel_right)

    invertible = np.isfinite(iv_all) & (iv_all > 0)
    n_dropped += int((~invertible).sum())

    ks_arr = sel_k[invertible]
    ivs_arr = iv_all[invertible]

    if ks_arr.size < 5:
        return ExpiryResult(
            days, T, forward, rate, fit.r2, False, "too_few_invertible_contracts",
            n_dropped=n_dropped,
        )

    # Vega weighting: the wings are wide and their mids are biased, so they must
    # not dominate a fit that matters most at the money.
    weights = np.maximum(
        greeks(
            forward, sel_strike[invertible], T, ivs_arr, rate, sel_right[invertible]
        ).vega,
        1e-9,
    )

    # Arbitrage checks run only where this expiry has data. A short expiry with
    # no quotable wings is extrapolating out there, and a violation in an
    # extrapolated region says nothing about the market.
    k_min, k_max = float(ks_arr.min()), float(ks_arr.max())
    support_grid = SURFACE_K_GRID[(SURFACE_K_GRID >= k_min) & (SURFACE_K_GRID <= k_max)]
    if support_grid.size < 8:
        support_grid = np.linspace(k_min, k_max, 41)

    surface = calibrate(
        ks_arr, np.square(ivs_arr) * T, T,
        weights=weights, seed=seed_params, n_dropped=n_dropped,
        check_grid=support_grid,
    )

    return ExpiryResult(
        days=days,
        T=T,
        forward=forward,
        rate=rate,
        parity_r2=fit.r2,
        accepted=surface.converged,
        reason="" if surface.converged else "svi_did_not_converge",
        params=surface.params if surface.converged else None,
        rmse_vol_points=surface.rmse_vol_points,
        butterfly_ok=surface.butterfly_ok,
        min_g=surface.min_g,
        n_used=surface.n_contracts_used,
        n_dropped=n_dropped,
        market_k=[round(float(v), 5) for v in ks_arr],
        market_iv=[round(float(v), 6) for v in ivs_arr],
        k_min=k_min,
        k_max=k_max,
        rate_source=rate_source,
    )


def compute_day(
    underlying: str,
    asof: date,
    *,
    spot: float,
    atm_vol: float,
    rate: float = 0.0435,
    bars: dict | None = None,
    iv30_history: list[float] | None = None,
    seed: int | None = None,
    prior_params: dict[int, SVIParams] | None = None,
    rows: list[dict] | None = None,
) -> dict:
    """Full pipeline for one underlying on one day. Returns the published record.

    ``rows`` accepts a real captured chain. When omitted the synthetic generator
    supplies one — the only difference between a real day and a demo day is
    where the rows came from, which is the property that makes the demo worth
    having.
    """
    if rows is None:
        rows, _truth = synthetic_chain(
            underlying, asof, spot=spot, rate=rate, atm_vol=atm_vol, seed=seed
        )
    snapshot_ts = rows[0]["snapshot_ts"]

    grouped = _group_by_expiry(rows, asof)
    prior_params = prior_params or {}

    # Pass one: parity regressions only, to find a rate the long end agrees on.
    long_rates = []
    for days in sorted(grouped):
        T = days / DAYS_PER_YEAR
        if T < MIN_T_FOR_RATE:
            continue
        by_strike = grouped[days]
        ks = np.array(sorted(by_strike), dtype=float)
        cm = np.array([_mid(by_strike[k].get("C", {}).get("bid"),
                            by_strike[k].get("C", {}).get("ask")) for k in ks])
        pm = np.array([_mid(by_strike[k].get("P", {}).get("bid"),
                            by_strike[k].get("P", {}).get("ask")) for k in ks])
        f = extract_forward(ks, cm, pm, T, spot=spot)
        if f.accepted and np.isfinite(f.rate):
            long_rates.append(f.rate)

    # Median, not mean: one bad expiry should not move it.
    robust_rate = float(np.median(long_rates)) if long_rates else None

    lo, hi = RATE_SANITY_BAND
    rate_extraction_ok = robust_rate is not None and lo <= robust_rate <= hi
    rate_note = ""
    curve = None
    if not rate_extraction_ok:
        # Not an assumption: the published Treasury curve for this date,
        # interpolated per expiry. The parity rate is preferred when the quotes
        # support it, because it carries the market's own financing and borrow.
        # When they do not, a real dated curve beats a number picked by hand.
        try:
            curve = load_curve(asof)
            rate_note = (
                f"parity rate extraction produced "
                f"{robust_rate if robust_rate is not None else float('nan'):.4f}, "
                f"outside the plausible band [{lo}, {hi}]. Using the FRED "
                f"constant-maturity Treasury curve as of {curve.as_of} instead, "
                "interpolated per expiry. Forwards are extracted from the chain "
                "and are unaffected."
            )
            robust_rate = curve.rate_for(30 / DAYS_PER_YEAR)
        except Exception as exc:
            rate_note = f"rate extraction failed and the Treasury curve is unavailable: {exc}"
            robust_rate = None

    results: list[ExpiryResult] = []
    for days in sorted(grouped):
        results.append(
            process_expiry(
                days, grouped[days], asof, spot, prior_params.get(days),
                (curve.rate_for(days / DAYS_PER_YEAR) if curve is not None
                 else robust_rate),
                force_override=not rate_extraction_ok,
            )
        )

    fitted = [r for r in results if r.accepted and r.params is not None]
    rejected = [r for r in results if not r.accepted]

    # The calendar condition is checked on the published grid, for the same
    # reason the butterfly condition is: past the quoted strikes the surface is
    # extrapolating, and a crossing out there is an artefact of extrapolation
    # rather than an arbitrage in anything we publish.
    calendar_ok, n_calendar_violations = check_calendar(
        [(r.T, r.params) for r in fitted],
        SURFACE_K_GRID,
        supports=[(r.k_min, r.k_max) for r in fitted],
    )

    # --- greeks and aggregates over the whole chain ------------------------
    # Batched per expiry: one vectorised greeks call each, rather than one call
    # per contract.
    fitted_by_days = {r.days: r for r in fitted}
    rows_by_days: dict[int, list[dict]] = {}
    for row in rows:
        days = (row["expiry"] - asof).days
        if days in fitted_by_days:
            rows_by_days.setdefault(days, []).append(row)

    agg_strikes, agg_gamma, agg_oi, agg_vol, agg_right = [], [], [], [], []
    for days, expiry_rows in rows_by_days.items():
        result = fitted_by_days[days]
        strikes_e = np.array([r["strike"] for r in expiry_rows], dtype=float)
        rights_e = np.array([r["right"] for r in expiry_rows])
        k_e = np.log(strikes_e / result.forward)

        iv_e = result.params.implied_vol(k_e, result.T)
        keep = (np.abs(k_e) <= MAX_ABS_K) & np.isfinite(iv_e) & (iv_e > 0)
        if not keep.any():
            continue

        gamma_e = greeks(
            result.forward, strikes_e[keep], result.T, iv_e[keep], result.rate,
            rights_e[keep],
        ).gamma

        agg_strikes.extend(strikes_e[keep].tolist())
        agg_gamma.extend(np.asarray(gamma_e).tolist())
        agg_right.extend(rights_e[keep].tolist())
        agg_oi.extend([r["open_interest"] for r, k in zip(expiry_rows, keep) if k])
        # np.nan_to_num inside chain_aggregates turns None into 0, which is the
        # right arithmetic and the wrong meaning. The coverage block below
        # reports how much of the chain had a real figure so nobody reads a
        # gamma number built on nothing.
        agg_vol.extend([r["volume"] for r, k in zip(expiry_rows, keep) if k])

    # Open interest is previous-day settled. The effective date travels with the
    # number so no downstream reader can mistake it for today's positioning.
    oi_effective = str(asof - timedelta(days=1))
    pain = max_pain(agg_strikes, agg_oi, agg_right)
    concentration = open_interest_concentration(agg_strikes, agg_oi)

    aggregates = chain_aggregates(
        agg_strikes, agg_gamma, agg_oi, agg_vol, agg_right,
        spot=spot, convention=DEFAULT_SIGN_CONVENTION, oi_effective_date=oi_effective,
    )

    # --- model-free implied variance, per expiry ---------------------------
    # Computed from the raw OTM strip, so it depends on no functional form.
    # Published beside the SVI at-the-money vol: agreement means the smile's
    # five parameters are doing no violence to the data, divergence means the
    # wings carry something the form cannot express.
    mfiv_by_expiry = []
    mfiv_rows = []
    for r in fitted:
        bs = grouped[r.days]
        ks = np.array(sorted(bs), dtype=float)
        cm = np.array([_mid(bs[k].get("C", {}).get("bid"),
                            bs[k].get("C", {}).get("ask")) for k in ks])
        pm = np.array([_mid(bs[k].get("P", {}).get("bid"),
                            bs[k].get("P", {}).get("ask")) for k in ks])
        mf = model_free_variance(ks, cm, pm, r.forward, r.rate, r.T)
        if np.isfinite(mf.total_variance):
            mfiv_by_expiry.append((r.T, mf.total_variance))
        mfiv_rows.append((r.days, mf))
    mfiv_lookup = {days: mf for days, mf in mfiv_rows}
    mfiv_30 = interpolate_30_day(mfiv_by_expiry, 30.0)

    # --- surface metrics ---------------------------------------------------
    fits = [(r.T, r.params) for r in fitted]
    term = surface_metrics.term_structure(fits)

    front = min(fitted, key=lambda r: abs(r.days - 30)) if fitted else None
    skew = (
        surface_metrics.skew_metrics(front.params, front.T, front.rate)
        if front is not None
        else {}
    )
    rank = surface_metrics.iv_rank_and_percentile(
        term.get("iv_30", np.nan), np.array(iv30_history or [])
    )

    rv = realized_vol.all_estimators(**bars) if bars else {}
    trend = momentum.all_metrics(bars["close"]) if bars else {}

    # Volatility implied *between* consecutive expiries, rather than from
    # today. A term structure that looks smooth in spot terms can imply
    # something strange for the period between two of its points.
    ordered = sorted(fitted, key=lambda r: r.T)
    forward_vols = []
    for a, b in zip(ordered, ordered[1:]):
        fv = surface_metrics.forward_volatility(
            a.T, float(a.params.implied_vol(0.0, a.T)),
            b.T, float(b.params.implied_vol(0.0, b.T)),
        )
        forward_vols.append(
            {"from_days": a.days, "to_days": b.days, "forward_vol": _clean(fv)}
        )

    quality = run_checks(
        rows,
        date=str(asof),
        underlying=underlying,
        snapshot_ts=snapshot_ts,
        fit_failures=[
            {"days": r.days, "reason": r.reason, "parity_r2": _clean(r.parity_r2)}
            for r in rejected
        ],
        expiries_fitted=len(fitted),
        expiries_rejected=len(rejected),
    )

    surface_payload = [
        {
            "days": r.days,
            "T": round(r.T, 6),
            "forward": round(r.forward, 4),
            "rate": round(r.rate, 6),
            "parity_r2": round(r.parity_r2, 6),
            "rate_source": r.rate_source,
            # F = S e^{(r-q)T}, so q falls out of a reliable forward and a real
            # rate. The forward survives a derived feed; this does too.
            # q = r - ln(F/S)/T divides by T, so it is only meaningful once T
            # is large enough that the division is not amplifying noise — the
            # same threshold, and the same reason, as the parity rate. At one
            # day this reads minus thirty-eight percent, which is arithmetic,
            # not a dividend.
            "implied_dividend_yield": _clean(
                implied_dividend_yield(r.forward, spot, r.T, r.rate)
                if r.T >= MIN_T_FOR_RATE else float("nan")
            ),
            "svi": {k: round(v, 8) for k, v in r.params.as_dict().items()},
            # Null outside the quoted range: the site draws no wing the market
            # did not price. A rendered surface that extends past its data is
            # showing the reader an extrapolation as though it were a quote.
            "k_min": round(r.k_min, 5),
            "k_max": round(r.k_max, 5),
            "iv_grid": [
                round(float(v), 6) if r.k_min <= float(kk) <= r.k_max else None
                for kk, v in zip(
                    SURFACE_K_GRID, r.params.implied_vol(SURFACE_K_GRID, r.T)
                )
            ],
            "rmse_vol_points": round(r.rmse_vol_points, 4),
            "mfiv_volatility": _clean(
                mfiv_lookup[r.days].volatility if r.days in mfiv_lookup else float("nan")
            ),
            "mfiv_n_strikes": (
                mfiv_lookup[r.days].n_strikes if r.days in mfiv_lookup else 0
            ),
            "implied_move": _clean(
                surface_metrics.implied_move(float(r.params.implied_vol(0.0, r.T)), r.T)
            ),
            "butterfly_ok": r.butterfly_ok,
            "min_g": round(r.min_g, 8),
            "n_contracts_used": r.n_used,
            "n_contracts_dropped": r.n_dropped,
            "market_k": r.market_k,
            "market_iv": r.market_iv,
        }
        for r in fitted
    ]

    n_total = len(rows)
    n_used = sum(r.n_used for r in fitted)

    return {
        "date": str(asof),
        "underlying": underlying,
        "snapshot_ts": snapshot_ts.isoformat(),
        "spot": round(spot, 4),
        "synthetic": all(r.get("source") == "SYNTHETIC" for r in rows),
        "feed": next((r.get("feed") for r in rows if r.get("feed")), None),
        "pipeline_version": PIPELINE_VERSION,
        "k_grid": [round(float(v), 5) for v in SURFACE_K_GRID],
        "surface": surface_payload,
        "rate": {
            "used": _clean(robust_rate),
            "extraction_ok": rate_extraction_ok,
            "treasury_curve": curve.as_dict() if curve is not None else None,
            "note": rate_note,
            "n_expiries_supplying": len(long_rates),
            "min_maturity_for_parity_rate_days": int(MIN_T_FOR_RATE * DAYS_PER_YEAR),
        },
        "arbitrage": {
            "calendar_ok": calendar_ok,
            "n_calendar_violations": n_calendar_violations,
            "n_expiries_butterfly_ok": sum(1 for r in fitted if r.butterfly_ok),
            "n_expiries_fitted": len(fitted),
            "worst_min_g": _clean(min((r.min_g for r in fitted), default=float("nan"))),
        },
        "metrics": {
            **{k: _clean(v) for k, v in term.items() if not isinstance(v, str)},
            "term_shape": term.get("term_shape"),
            **{k: _clean(v) for k, v in skew.items()},
            **{k: _clean(v) for k, v in rank.items()},
            **{k: _clean(v) for k, v in rv.items()},
            **{k: _clean(v) for k, v in trend.items()},
            "mfiv_30": _clean(mfiv_30),
            # The diagnostic: model-free against parametric at 30 days.
            "mfiv_minus_svi_30": _clean(
                (mfiv_30 - term.get("iv_30"))
                if (np.isfinite(mfiv_30) and term.get("iv_30") is not None)
                else float("nan")
            ),
            "implied_move_30d": _clean(
                surface_metrics.implied_move(term.get("iv_30") or float("nan"),
                                             30 / DAYS_PER_YEAR)
            ),
        },
        "forward_vols": forward_vols,
        "aggregates": {
            **{k: _clean(v) for k, v in aggregates.as_dict().items()
               if not isinstance(v, str)},
            "max_pain_strike": _clean(pain),
            **{k: _clean(v) for k, v in concentration.items()},
            "gex_sign_convention": aggregates.sign_convention,
            "oi_effective_date": aggregates.oi_effective_date,
            "gex_by_strike": [
                {"strike": k, "gex": round(v, 2)}
                for k, v in sorted(aggregates.gex_by_strike.items())
            ],
        },
        "coverage": {
            "open_interest_known": sum(
                1 for r in rows if r.get("open_interest") is not None
            ),
            "n_chain_rows": n_total,
            "n_contracts_in_fits": n_used,
            "n_contracts_dropped": sum(r.n_dropped for r in fitted),
            "excluded_share": round(1.0 - n_used / max(n_total, 1), 4),
        },
        "quality": quality.as_dict(),
    }


def _clean(value):
    """JSON has no NaN. Publish null instead, so a missing number stays missing."""
    if isinstance(value, (bool, str)) or value is None:
        return value
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    return None if not np.isfinite(v) else round(v, 8)


def build_archive(
    n_days: int,
    out_dir: Path,
    underlying: str = "SPY",
    end: date | None = None,
) -> list[dict]:
    """Run a run of consecutive trading days to seed an archive with history.

    Volatility follows a mean-reverting path so IV rank and percentile have a
    real distribution behind them rather than a flat line.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20260312)

    bars = synthetic_ohlcv(n_days=n_days + 300, annual_vol=0.145, seed=99)

    # Mean-reverting implied vol, floored well away from zero.
    vol_path = np.empty(n_days)
    v = 0.152
    for i in range(n_days):
        v += 0.12 * (0.152 - v) + rng.normal(0.0, 0.006)
        vol_path[i] = max(v, 0.075)

    days: list[dict] = []
    iv30_history: list[float] = []
    prior: dict[int, SVIParams] = {}

    # Walk backwards from the most recent weekday so "today" is the last entry.
    calendar_days = []
    cursor = end or date.today()
    while len(calendar_days) < n_days:
        if cursor.weekday() < 5:
            calendar_days.append(cursor)
        cursor -= timedelta(days=1)
    calendar_days.reverse()

    for i, asof in enumerate(calendar_days):
        window = bars["close"][: 300 + i + 1]
        spot = float(window[-1])
        day_bars = {k: v[: 300 + i + 1] for k, v in bars.items()}

        record = compute_day(
            underlying,
            asof,
            spot=spot,
            atm_vol=float(vol_path[i]),
            bars=day_bars,
            iv30_history=iv30_history.copy(),
            seed=1000 + i,
            prior_params=prior,
        )
        prior = {
            entry["days"]: SVIParams(**entry["svi"]) for entry in record["surface"]
        }
        iv30 = record["metrics"].get("iv_30")
        if iv30 is not None:
            iv30_history.append(iv30)

        (out_dir / f"{asof}.json").write_text(json.dumps(record, separators=(",", ":")))
        days.append(record)

    return days


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--out", type=Path, default=Path("site/public/data"))
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="last session in the archive (YYYY-MM-DD); defaults to today",
    )
    args = parser.parse_args()

    archive_dir = args.out / "archive"
    records = build_archive(args.days, archive_dir, end=args.end)
    latest = records[-1]

    (args.out / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        # Over the whole archive, not one chain: `rows` is compute_day()'s
        # per-contract list and does not exist in this scope. Each record
        # already carries the verdict for its own day, so the index is
        # synthetic only if every day in it is.
        "synthetic": all(r.get("synthetic") for r in records),
        "feed": next((r.get("feed") for r in records if r.get("feed")), None),
        "underlying": latest["underlying"],
        "latest_date": latest["date"],
        "n_days": len(records),
        "dates": [r["date"] for r in records],
        "contract_days": sum(r["coverage"]["n_chain_rows"] for r in records),
        "history": [
            {
                "date": r["date"],
                "spot": r["spot"],
                "iv_30": r["metrics"].get("iv_30"),
                "iv_60": r["metrics"].get("iv_60"),
                "iv_90": r["metrics"].get("iv_90"),
                "rv_cc_21": r["metrics"].get("rv_cc_21"),
                "rv_yz_21": r["metrics"].get("rv_yz_21"),
                "rv_park_21": r["metrics"].get("rv_park_21"),
                "rv_park_cc_ratio_21": r["metrics"].get("rv_park_cc_ratio_21"),
                "risk_reversal_25d": r["metrics"].get("risk_reversal_25d"),
                "butterfly_25d": r["metrics"].get("butterfly_25d"),
                "term_slope_90_30": r["metrics"].get("term_slope_90_30"),
                "gex_total": r["aggregates"].get("gex_total"),
                "crossed_share": r["quality"].get("crossed_share"),
                "zero_bid_share": r["quality"].get("zero_bid_share"),
                "excluded_share": r["coverage"].get("excluded_share"),
                "worst_min_g": r["arbitrage"].get("worst_min_g"),
                "n_expiries_fitted": r["arbitrage"].get("n_expiries_fitted"),
                "qc_passed": r["quality"].get("passed"),
            }
            for r in records
        ],
    }
    (args.out / "index.json").write_text(json.dumps(index, separators=(",", ":")))

    print(f"wrote {len(records)} days to {args.out}")
    print(f"latest: {latest['date']}  spot={latest['spot']}")
    print(f"  expiries fitted   : {latest['arbitrage']['n_expiries_fitted']}")
    print(f"  butterfly ok      : {latest['arbitrage']['n_expiries_butterfly_ok']}")
    print(f"  calendar ok       : {latest['arbitrage']['calendar_ok']}")
    print(f"  IV30              : {latest['metrics'].get('iv_30')}")
    print(f"  contract-days     : {index['contract_days']:,}")


if __name__ == "__main__":
    main()
