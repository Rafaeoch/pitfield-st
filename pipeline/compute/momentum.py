"""Descriptive statistics of the price series.

Deliberately not a technical-indicator library. RSI, MACD and stochastics are
absent because they are chart-reading conventions with no standing in the
literature, and this project's own specification says that anything belonging
on a retail trading product does not belong here.

What is here has provenance. Twelve-minus-one momentum is the Jegadeesh and
Titman construction — the trailing twelve-month return excluding the most
recent month, which is skipped because of the well-documented short-term
reversal that otherwise contaminates it. Drawdown, realized skewness and
downside semi-deviation are descriptions of a return distribution, not signals
derived from one.

Nothing here says what to do. It says what happened.
"""

from __future__ import annotations

import numpy as np

TRADING_DAYS = 252

# Standard lookbacks, in trading days.
HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}


def trailing_return(close: np.ndarray, days: int) -> float:
    """Simple total return over the trailing window."""
    close = np.asarray(close, dtype=float)
    if close.size < days + 1:
        return np.nan
    return float(close[-1] / close[-days - 1] - 1.0)


def momentum_12_1(close: np.ndarray) -> float:
    """Jegadeesh-Titman: twelve-month return, skipping the most recent month.

    The skip is the whole point of the construction. Including the last month
    mixes in short-term reversal, which runs the opposite way to momentum and
    partially cancels it.
    """
    close = np.asarray(close, dtype=float)
    if close.size < 253:
        return np.nan
    return float(close[-22] / close[-253] - 1.0)


def distance_from_moving_average(close: np.ndarray, window: int = 200) -> float:
    """Current price relative to its moving average, as a fraction."""
    close = np.asarray(close, dtype=float)
    if close.size < window:
        return np.nan
    ma = float(np.mean(close[-window:]))
    return float(close[-1] / ma - 1.0) if ma > 0 else np.nan


def drawdown(close: np.ndarray, window: int | None = None) -> tuple[float, float]:
    """(current drawdown from the running peak, deepest drawdown in the window).

    Both are negative or zero. Computed on the running maximum rather than the
    window maximum, so a drawdown that began before the window still counts
    from its true peak.
    """
    close = np.asarray(close, dtype=float)
    if window is not None:
        close = close[-window:]
    if close.size < 2:
        return np.nan, np.nan
    peak = np.maximum.accumulate(close)
    dd = close / peak - 1.0
    return float(dd[-1]), float(dd.min())


def realized_moments(close: np.ndarray, window: int = 252) -> dict:
    """Skewness and excess kurtosis of daily log returns.

    Equity index returns are reliably left-skewed and fat-tailed, and both are
    the reason a Gaussian model misprices the wings. Publishing them next to
    the implied skew lets a reader compare what the market charges for tail
    risk against what the tail actually did.
    """
    close = np.asarray(close, dtype=float)
    if close.size < window + 1:
        window = close.size - 1
    if window < 30:
        return {"realized_skew": np.nan, "realized_excess_kurtosis": np.nan}

    r = np.diff(np.log(close[-window - 1 :]))
    mu, sd = r.mean(), r.std(ddof=1)
    if sd <= 0:
        return {"realized_skew": np.nan, "realized_excess_kurtosis": np.nan}
    z = (r - mu) / sd
    return {
        "realized_skew": float(np.mean(z**3)),
        "realized_excess_kurtosis": float(np.mean(z**4) - 3.0),
    }


def downside_deviation(close: np.ndarray, window: int = 252) -> float:
    """Annualised standard deviation of negative daily returns only.

    Volatility treats a rally and a crash identically. This does not, which is
    the whole reason it is reported alongside.
    """
    close = np.asarray(close, dtype=float)
    if close.size < 32:
        return np.nan
    r = np.diff(np.log(close[-window - 1 :])) if close.size > window else np.diff(np.log(close))
    negative = r[r < 0]
    if negative.size < 10:
        return np.nan
    return float(np.sqrt(np.mean(np.square(negative)) * TRADING_DAYS))


def all_metrics(close: np.ndarray) -> dict:
    """Every descriptive statistic, in one table."""
    close = np.asarray(close, dtype=float)
    out: dict[str, float] = {}
    for label, days in HORIZONS.items():
        out[f"return_{label}"] = trailing_return(close, days)
    out["momentum_12_1"] = momentum_12_1(close)
    out["dist_from_ma50"] = distance_from_moving_average(close, 50)
    out["dist_from_ma200"] = distance_from_moving_average(close, 200)
    current, deepest = drawdown(close, 252)
    out["drawdown_current"] = current
    out["drawdown_max_252"] = deepest
    out["downside_deviation_252"] = downside_deviation(close)
    out.update(realized_moments(close))
    return out
