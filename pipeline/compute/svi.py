"""Raw SVI calibration in total implied variance, with arbitrage diagnostics.

We work in total implied variance ``w = sigma^2 T`` against log-moneyness
``k = ln(K/F)``, using Gatheral's raw parameterisation:

    w(k) = a + b [ rho (k - m) + sqrt( (k - m)^2 + sigma^2 ) ]

subject to

    b >= 0,   |rho| < 1,   sigma > 0,   a + b sigma sqrt(1 - rho^2) >= 0

The last constraint is exactly ``min_k w(k) >= 0``. The minimum sits at
``k - m = -rho sigma / sqrt(1 - rho^2)``, where the wing slopes ``b(rho +/- 1)``
balance; substituting gives ``w_min = a + b sigma sqrt(1 - rho^2)``. That is
derived rather than asserted, because every equation on this site has to be one
its author can put on a whiteboard.

Arbitrage diagnostics are computed for every fitted surface and published
alongside it, including the failures. A surface with negative implied density,
shown next to the constrained fit, is worth more than an essay about why
arbitrage constraints matter.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from typing import NamedTuple

import numpy as np
from scipy.optimize import minimize

# Dense grid on which Durrleman's condition and the calendar condition are
# evaluated. Wide enough to cover the fitted wings, fine enough that a localised
# density violation cannot hide between grid points.
CHECK_GRID = np.linspace(-1.5, 1.5, 601)

# Numerical floor for g(k): values inside +/-EPS_G count as zero, not violations.
EPS_G = -1e-8


@dataclass(frozen=True)
class SVIParams:
    """One expiry's raw-SVI parameters, in total-variance space."""

    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def as_dict(self) -> dict:
        return asdict(self)

    # --- the curve and its derivatives, all analytic -----------------------
    #
    # With x = k - m and s = sqrt(x^2 + sigma^2):
    #     w   = a + b (rho x + s)
    #     w'  = b (rho + x / s)
    #     w'' = b (1/s - x^2/s^3) = b sigma^2 / s^3

    def total_variance(self, k):
        x = np.asarray(k, dtype=float) - self.m
        s = np.sqrt(np.square(x) + self.sigma**2)
        return self.a + self.b * (self.rho * x + s)

    def d_total_variance(self, k):
        x = np.asarray(k, dtype=float) - self.m
        s = np.sqrt(np.square(x) + self.sigma**2)
        return self.b * (self.rho + x / s)

    def d2_total_variance(self, k):
        x = np.asarray(k, dtype=float) - self.m
        s = np.sqrt(np.square(x) + self.sigma**2)
        return self.b * self.sigma**2 / np.power(s, 3)

    def implied_vol(self, k, T: float):
        """Convert the fitted total variance back to an annualised volatility."""
        return np.sqrt(np.maximum(self.total_variance(k), 0.0) / T)

    @property
    def min_total_variance(self) -> float:
        return self.a + self.b * self.sigma * np.sqrt(1.0 - self.rho**2)


def durrleman_g(params: SVIParams, k=CHECK_GRID):
    """Durrleman's function g(k). Negative anywhere means negative density.

        g(k) = ( 1 - k w'(k) / (2 w(k)) )^2
               - ( w'(k)^2 / 4 ) ( 1/w(k) + 1/4 )
               + w''(k) / 2

    g(k) >= 0 for all k is equivalent to the fitted smile being free of
    butterfly arbitrage.
    """
    k = np.asarray(k, dtype=float)
    w = params.total_variance(k)
    wp = params.d_total_variance(k)
    wpp = params.d2_total_variance(k)

    with np.errstate(divide="ignore", invalid="ignore"):
        term1 = np.square(1.0 - k * wp / (2.0 * w))
        term2 = (np.square(wp) / 4.0) * (1.0 / w + 0.25)
        g = term1 - term2 + wpp / 2.0

    return np.where(w > 0.0, g, -np.inf)


def check_butterfly(params: SVIParams, k=CHECK_GRID) -> tuple[bool, float]:
    """Return (butterfly_ok, min_g) over the check grid."""
    g = durrleman_g(params, k)
    min_g = float(np.min(g))
    return bool(min_g >= EPS_G), min_g


class CalendarCheck(NamedTuple):
    """Whether the surface crosses in maturity, and by how much.

    Read the fields by name. This deliberately does not unpack as a pair: it
    carries four values, and a caller writing ``ok, n = check_calendar(...)``
    should fail loudly rather than silently bind the wrong things.
    """

    ok: bool
    n_violations: int
    worst_dw: float = 0.0
    worst_vol_points: float = 0.0


def check_calendar(
    fits: list[tuple[float, SVIParams]],
    k=CHECK_GRID,
    supports: list[tuple[float, float]] | None = None,
) -> CalendarCheck:
    """Total variance must be non-decreasing in T at fixed k.

    ``fits`` is a list of (T, params), any order. Returns
    (calendar_ok, n_violations) where a violation is one (k, adjacent-expiry)
    pair whose total variance decreases with maturity.

    ``supports`` optionally gives each fit's (k_min, k_max) — the log-moneyness
    range where that expiry actually had quotable contracts. When supplied, a
    pair of expiries is only compared where *both* had data.

    That restriction is not a way of hiding violations; it is the difference
    between a violation and an artefact. A 7-day expiry has no quotes 30% out of
    the money, so its fitted value there is pure extrapolation, and comparing
    that extrapolation against a 14-day expiry that *does* have quotes there
    reports a crossing between one real curve and one invented one. We check —
    and publish — only where the market gave us something to check.
    """
    if len(fits) < 2:
        return True, 0

    k = np.asarray(k, dtype=float)
    order = sorted(range(len(fits)), key=lambda i: fits[i][0])
    ordered = [fits[i] for i in order]
    ordered_supports = (
        [supports[i] for i in order] if supports is not None else None
    )

    n_violations = 0
    worst_dw = 0.0
    worst_vol_points = 0.0
    for i in range(len(ordered) - 1):
        (T_near, near), (T_far, far) = ordered[i], ordered[i + 1]
        mask = np.ones_like(k, dtype=bool)
        if ordered_supports is not None:
            lo = max(ordered_supports[i][0], ordered_supports[i + 1][0])
            hi = min(ordered_supports[i][1], ordered_supports[i + 1][1])
            mask = (k >= lo) & (k <= hi)
        if not mask.any():
            continue
        diff = far.total_variance(k[mask]) - near.total_variance(k[mask])
        bad = diff < -1e-10
        n_violations += int(np.sum(bad))
        if bad.any():
            j = int(np.argmin(diff))
            dw = float(diff[j])
            worst_dw = min(worst_dw, dw)
            # A count says a surface crosses. It cannot say whether the crossing
            # is a tradeable inconsistency or a rounding artefact, and those
            # want different responses. Restating the worst crossing as the
            # implied-vol move that would repair it puts it in units a reader
            # already has intuition for.
            w_far = float(far.total_variance(k[mask][j]))
            if T_far > 0:
                v_now = np.sqrt(max(w_far, 1e-12) / T_far)
                v_fix = np.sqrt(max(w_far - dw, 1e-12) / T_far)
                worst_vol_points = max(worst_vol_points, abs(v_fix - v_now) * 100.0)

    return CalendarCheck(
        ok=n_violations == 0,
        n_violations=n_violations,
        worst_dw=worst_dw,
        worst_vol_points=worst_vol_points,
    )


@dataclass(frozen=True)
class SurfaceFit:
    """A calibrated expiry plus everything a reader needs to judge it."""

    expiry_T: float
    params: SVIParams
    rmse_vol_points: float
    butterfly_ok: bool
    min_g: float
    n_contracts_used: int
    n_contracts_dropped: int
    converged: bool

    def as_dict(self) -> dict:
        out = {"expiry_T": self.expiry_T, **self.params.as_dict()}
        out.update(
            {
                "rmse_vol_points": self.rmse_vol_points,
                "butterfly_ok": self.butterfly_ok,
                "min_g": self.min_g,
                "n_contracts_used": self.n_contracts_used,
                "n_contracts_dropped": self.n_contracts_dropped,
                "converged": self.converged,
            }
        )
        return out


def _seed_from_data(k: np.ndarray, w: np.ndarray) -> list[np.ndarray]:
    """Heuristic starting points. Multi-start because raw SVI has flat valleys."""
    w_min = max(float(np.min(w)), 1e-6)
    k_at_min = float(k[int(np.argmin(w))])
    spread = max(float(np.ptp(k)), 0.1)

    return [
        np.array([w_min * 0.5, 0.1, -0.5, k_at_min, 0.1]),
        np.array([w_min * 0.9, 0.05, -0.3, 0.0, 0.2]),
        np.array([w_min * 0.1, 0.2, -0.7, k_at_min, spread / 4.0]),
        np.array([0.0, 0.15, 0.0, 0.0, 0.3]),
    ]


def calibrate(
    k,
    total_variance,
    T: float,
    *,
    weights=None,
    seed: SVIParams | None = None,
    n_dropped: int = 0,
    check_grid=CHECK_GRID,
    floor: SVIParams | None = None,
    floor_support: tuple[float, float] | None = None,
) -> SurfaceFit:
    """Calibrate raw SVI to one expiry by constrained weighted least squares.

    ``weights`` should be vega or inverse relative spread — never equal weights,
    which let illiquid wings dominate a fit that matters most at the money.
    ``seed`` is the previous day's parameters when available; surfaces move
    slowly, so yesterday's fit is a good starting point and keeps the parameter
    time series stable rather than jumping between equivalent optima.

    ``floor`` is the previously fitted, shorter-dated expiry. When supplied,
    this fit is constrained so its total variance never falls below that one
    across ``floor_support``, which makes calendar arbitrage impossible by
    construction rather than something to detect afterwards.

    It is not free, and the cost is the point. Between expiries a day or two
    apart the true variance increment is smaller than the noise in the quotes,
    so the constraint is not recovering an ordering the market expressed; it is
    imposing one the market did not. The price is paid in fit: the surface must
    match observed prices less well to satisfy it. Whether that trade is worth
    making is an empirical question about the size of the RMSE increase, which
    is why both numbers are published.

    ``check_grid`` is the log-moneyness range the butterfly condition is
    evaluated on. It should be the range the surface is *published* over, not
    something wider: SVI extrapolates past the quoted strikes with no data
    constraining it, and reporting a violation out there says more about
    extrapolation than about the market. We claim arbitrage-freedom only where
    we publish a number.
    """
    k = np.asarray(k, dtype=float)
    w = np.asarray(total_variance, dtype=float)

    good = np.isfinite(k) & np.isfinite(w) & (w > 0.0)
    k, w = k[good], w[good]
    n_used = int(k.size)

    if weights is None:
        weights = np.ones_like(k)
    else:
        weights = np.asarray(weights, dtype=float)[good]
    weights = np.where(np.isfinite(weights) & (weights > 0), weights, 0.0)
    if weights.sum() <= 0:
        weights = np.ones_like(k)
    weights = weights / weights.sum()

    if n_used < 5:
        empty = SVIParams(np.nan, np.nan, np.nan, np.nan, np.nan)
        return SurfaceFit(T, empty, np.nan, False, np.nan, n_used, n_dropped, False)

    def objective(theta: np.ndarray) -> float:
        a, b, rho, m, sig = theta
        x = k - m
        s = np.sqrt(np.square(x) + sig**2)
        model = a + b * (rho * x + s)
        return float(np.sum(weights * np.square(model - w)))

    # a + b sigma sqrt(1 - rho^2) >= 0  ->  w(k) >= 0 everywhere.
    constraints = [
        {
            "type": "ineq",
            "fun": lambda th: th[0] + th[1] * th[4] * np.sqrt(max(1.0 - th[2] ** 2, 0.0)),
        }
    ]

    if floor is not None:
        # Evaluated where both expiries have quotes and where this surface is
        # published. Constraining it out in the extrapolated wings would trade
        # real fit quality for a guarantee about numbers nobody can see.
        lo, hi = floor_support if floor_support is not None else (-np.inf, np.inf)
        grid = np.asarray(check_grid, dtype=float)
        grid = grid[(grid >= lo) & (grid <= hi)]
        if grid.size:
            floor_w = floor.total_variance(grid)

            def calendar_floor(th: np.ndarray, g=grid, fw=floor_w) -> np.ndarray:
                a, b, rho, m, sig = th
                x = g - m
                return (a + b * (rho * x + np.sqrt(np.square(x) + sig**2))) - fw

            constraints.append({"type": "ineq", "fun": calendar_floor})
    bounds = [
        (-2.0, 2.0),        # a
        (0.0, 10.0),        # b >= 0
        (-0.9999, 0.9999),  # |rho| < 1
        (-2.0, 2.0),        # m
        (1e-6, 5.0),        # sigma > 0
    ]

    starts = _seed_from_data(k, w)
    warm_started = seed is not None and np.all(
        np.isfinite(list(seed.as_dict().values()))
    )
    if warm_started:
        # Yesterday's parameters are a good starting point — surfaces move
        # slowly — so a warm start needs only one cold fallback behind it
        # rather than the full multi-start sweep.
        starts = [np.array([seed.a, seed.b, seed.rho, seed.m, seed.sigma])] + starts[:1]

    # A fit this good relative to the data's own scale cannot be meaningfully
    # improved by another start, so stop looking.
    scale = float(np.sum(weights * np.square(w)))
    good_enough = 1e-10 * scale

    best, best_loss, converged = None, np.inf, False
    for x0 in starts:
        x0 = np.clip(x0, [b[0] for b in bounds], [b[1] for b in bounds])
        try:
            res = minimize(
                objective,
                x0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 500, "ftol": 1e-12},
            )
        except Exception:
            continue
        if res.success and res.fun < best_loss:
            best, best_loss, converged = res.x, float(res.fun), True
            if best_loss <= good_enough:
                break
        elif best is None and np.all(np.isfinite(res.x)):
            best, best_loss = res.x, float(res.fun)

    if best is None:
        empty = SVIParams(np.nan, np.nan, np.nan, np.nan, np.nan)
        return SurfaceFit(T, empty, np.nan, False, np.nan, n_used, n_dropped, False)

    params = SVIParams(*(float(v) for v in best))

    # RMSE is reported in volatility points, not total-variance units: that is
    # the unit a reader can judge, and it is what /quality publishes.
    fitted_vol = params.implied_vol(k, T)
    market_vol = np.sqrt(w / T)
    rmse = float(np.sqrt(np.mean(np.square(fitted_vol - market_vol)))) * 100.0

    butterfly_ok, min_g = check_butterfly(params, check_grid)

    return SurfaceFit(
        expiry_T=T,
        params=params,
        rmse_vol_points=rmse,
        butterfly_ok=butterfly_ok,
        min_g=min_g,
        n_contracts_used=n_used,
        n_contracts_dropped=n_dropped,
        converged=converged,
    )
