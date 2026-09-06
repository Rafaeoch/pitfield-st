"""Chain aggregates: gamma exposure, flip point, positioning ratios.

The sign convention on GEX is an assumption, not a fact. Read
:data:`DEALER_SIGN_CONVENTIONS` before using any number out of this module.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# GEX requires an assumption about which side of the customer's trade the dealer
# is on. The common convention treats dealers as long gamma from calls and short
# from puts, which assumes customers buy puts and sell calls. That is a
# generalisation about flow, not an observed fact — we cannot see the dealer's
# book. The assumption travels with the number in the output schema and is
# stated on /methodology and on every page that displays GEX.
DEALER_SIGN_CONVENTIONS = {
    # Dealers long gamma from calls, short from puts. The market-standard choice.
    "long_call_short_put": {"C": +1.0, "P": -1.0},
    # Every contract signed the same way: no flow assumption, pure gross gamma.
    "gross": {"C": +1.0, "P": +1.0},
}

DEFAULT_SIGN_CONVENTION = "long_call_short_put"


@dataclass(frozen=True)
class ChainAggregates:
    gex_total: float
    gex_by_strike: dict[float, float]
    gamma_flip_strike: float
    max_gamma_strike: float
    max_oi_strike: float
    put_call_ratio_volume: float
    put_call_ratio_oi: float
    volume_to_oi: float
    sign_convention: str
    oi_effective_date: str | None

    def as_dict(self) -> dict:
        return {
            "gex_total": self.gex_total,
            "gamma_flip_strike": self.gamma_flip_strike,
            "max_gamma_strike": self.max_gamma_strike,
            "max_oi_strike": self.max_oi_strike,
            "put_call_ratio_volume": self.put_call_ratio_volume,
            "put_call_ratio_oi": self.put_call_ratio_oi,
            "volume_to_oi": self.volume_to_oi,
            "gex_sign_convention": self.sign_convention,
            "oi_effective_date": self.oi_effective_date,
        }


def gamma_exposure(
    strikes,
    gamma,
    open_interest,
    right,
    spot: float,
    multiplier=100,
    convention: str = DEFAULT_SIGN_CONVENTION,
):
    """Dollar gamma per 1% move in spot, by strike.

        GEX(K) = Gamma(K) * OI(K) * multiplier * S^2 * 0.01

    ``open_interest`` is previous-day settled. Whatever consumes this must
    timestamp it to the OI's effective date, not to the snapshot date.
    """
    if convention not in DEALER_SIGN_CONVENTIONS:
        raise ValueError(
            f"unknown sign convention {convention!r}; "
            f"expected one of {sorted(DEALER_SIGN_CONVENTIONS)}"
        )
    signs = DEALER_SIGN_CONVENTIONS[convention]

    strikes = np.asarray(strikes, dtype=float)
    gamma = np.asarray(gamma, dtype=float)
    oi = np.asarray(open_interest, dtype=float)
    right = np.asarray(right)
    multiplier = np.broadcast_to(np.asarray(multiplier, dtype=float), strikes.shape)

    sign = np.where(right == "C", signs["C"], signs["P"])
    contribution = sign * gamma * oi * multiplier * (spot**2) * 0.01
    contribution = np.where(np.isfinite(contribution), contribution, 0.0)

    unique = np.unique(strikes[np.isfinite(strikes)])
    by_strike = {
        float(k): float(np.sum(contribution[strikes == k])) for k in unique
    }
    return by_strike


def gamma_flip(by_strike: dict[float, float]) -> float:
    """Strike at which cumulative GEX crosses zero, linearly interpolated.

    Returns NaN when the cumulative profile never changes sign — a real state,
    not an error, and it must not be reported as a strike of 0.
    """
    if not by_strike:
        return np.nan
    strikes = np.array(sorted(by_strike))
    cumulative = np.cumsum([by_strike[k] for k in strikes])

    sign_change = np.where(np.diff(np.sign(cumulative)) != 0)[0]
    if sign_change.size == 0:
        return np.nan

    i = int(sign_change[0])
    y0, y1 = cumulative[i], cumulative[i + 1]
    if y1 == y0:
        return float(strikes[i])
    weight = -y0 / (y1 - y0)
    return float(strikes[i] + weight * (strikes[i + 1] - strikes[i]))


def max_pain(strikes, open_interest, right, multiplier=100) -> float:
    """The strike at which the total value of open contracts is smallest.

    Folklore says price gravitates here at expiry. The folklore is not the
    reason to compute it: it is a legible summary of where open interest
    actually sits, and it is cheap. No claim is made about it, here or on the
    site.

    For each candidate settlement price S, total intrinsic across the chain is
    sum over calls of max(S-K,0)*OI plus over puts of max(K-S,0)*OI. Max pain
    is the S minimising that.
    """
    strikes = np.asarray(strikes, dtype=float)
    oi = np.asarray(open_interest, dtype=float)
    right = np.asarray(right)
    known = np.isfinite(oi) & np.isfinite(strikes)
    if known.sum() < 3:
        return np.nan
    strikes, oi, right = strikes[known], oi[known], right[known]

    candidates = np.unique(strikes)
    is_call = right == "C"
    # Vectorised over candidates: (n_candidates, n_contracts).
    payoff = np.where(
        is_call,
        np.maximum(candidates[:, None] - strikes[None, :], 0.0),
        np.maximum(strikes[None, :] - candidates[:, None], 0.0),
    )
    total = (payoff * oi[None, :] * multiplier).sum(axis=1)
    return float(candidates[int(np.argmin(total))])


def open_interest_concentration(strikes, open_interest) -> dict:
    """How concentrated open interest is across strikes.

    A chain whose open interest sits in five strikes behaves differently from
    one where it is spread over two hundred, and the aggregate figures look
    identical either way. Herfindahl is the standard concentration measure;
    the top-five share is the same fact in a form anyone can read.
    """
    strikes = np.asarray(strikes, dtype=float)
    oi = np.asarray(open_interest, dtype=float)
    known = np.isfinite(oi) & (oi > 0)
    if known.sum() < 5:
        return {"oi_herfindahl": np.nan, "oi_top5_share": np.nan}

    by_strike: dict[float, float] = {}
    for k, v in zip(strikes[known], oi[known]):
        by_strike[float(k)] = by_strike.get(float(k), 0.0) + float(v)
    values = np.array(sorted(by_strike.values(), reverse=True))
    share = values / values.sum()
    return {
        "oi_herfindahl": float(np.sum(np.square(share))),
        "oi_top5_share": float(share[:5].sum()),
    }


def chain_aggregates(
    strikes,
    gamma,
    open_interest,
    volume,
    right,
    spot: float,
    multiplier=100,
    convention: str = DEFAULT_SIGN_CONVENTION,
    oi_effective_date: str | None = None,
) -> ChainAggregates:
    """Full aggregate set for one underlying on one day."""
    strikes = np.asarray(strikes, dtype=float)
    oi = np.nan_to_num(np.asarray(open_interest, dtype=float))
    vol = np.nan_to_num(np.asarray(volume, dtype=float))
    right = np.asarray(right)

    by_strike = gamma_exposure(
        strikes, gamma, oi, right, spot, multiplier, convention
    )

    gamma_abs = {}
    for k in np.unique(strikes[np.isfinite(strikes)]):
        mask = strikes == k
        gamma_abs[float(k)] = float(
            np.nansum(np.asarray(gamma, dtype=float)[mask] * oi[mask])
        )

    oi_by_strike: dict[float, float] = {}
    for k in np.unique(strikes[np.isfinite(strikes)]):
        oi_by_strike[float(k)] = float(np.sum(oi[strikes == k]))

    is_put, is_call = right == "P", right == "C"
    call_vol, put_vol = float(vol[is_call].sum()), float(vol[is_put].sum())
    call_oi, put_oi = float(oi[is_call].sum()), float(oi[is_put].sum())
    total_oi, total_vol = call_oi + put_oi, call_vol + put_vol

    return ChainAggregates(
        gex_total=float(sum(by_strike.values())),
        gex_by_strike=by_strike,
        gamma_flip_strike=gamma_flip(by_strike),
        max_gamma_strike=(
            max(gamma_abs, key=gamma_abs.get) if gamma_abs else np.nan
        ),
        max_oi_strike=(
            max(oi_by_strike, key=oi_by_strike.get) if oi_by_strike else np.nan
        ),
        put_call_ratio_volume=put_vol / call_vol if call_vol > 0 else np.nan,
        put_call_ratio_oi=put_oi / call_oi if call_oi > 0 else np.nan,
        # > 1 indicates new positioning rather than closing.
        volume_to_oi=total_vol / total_oi if total_oi > 0 else np.nan,
        sign_convention=convention,
        oi_effective_date=oi_effective_date,
    )
