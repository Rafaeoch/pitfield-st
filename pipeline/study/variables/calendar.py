"""Calendar effects — the Tier 4 control group.

These are the honest contrast. Some have survived scrutiny better than anything
in the celestial tiers, and a harness that cannot tell them apart from lunar
phase is not measuring anything.
"""
from __future__ import annotations
import numpy as np
import pandas_market_calendars as mcal
from .base import binary, result

_NYSE = mcal.get_calendar("NYSE")


def _sessions(dates):
    d = np.asarray(dates, dtype="datetime64[D]")
    sched = _NYSE.schedule(start_date=str(d.min()), end_date=str(d.max()))
    return np.array([np.datetime64(x.date(), "D") for x in sched.index])


def compute(dates, spec=None) -> dict:
    hid = spec["id"]
    d = np.asarray(dates, dtype="datetime64[D]")
    months = d.astype("datetime64[M]")
    month_no = months.astype(int) % 12 + 1
    dow = (d.astype(int) - 4) % 7          # 1970-01-01 was a Thursday

    if hid == "january_effect":
        f = month_no == 1
        return result(d, f.astype(float), binary(f, "january", "rest"))

    if hid == "sell_in_may":
        f = (month_no >= 5) & (month_no <= 10)
        return result(d, f.astype(float), binary(f, "may_to_october", "rest"))

    if hid == "turn_of_month":
        dom = (d - months).astype(int) + 1
        days_in_month = ((months + 1).astype("datetime64[D]") - months.astype("datetime64[D]")).astype(int)
        f = (dom <= 3) | (dom > days_in_month - 1)
        return result(d, f.astype(float), binary(f, "turn", "rest"))

    if hid == "day_of_week":
        names = np.array(["monday", "tuesday", "wednesday", "thursday",
                          "friday", "saturday", "sunday"])
        return result(d, dow.astype(float), names[dow])

    if hid == "santa_claus_rally":
        day = (d - months).astype(int) + 1
        f = ((month_no == 12) & (day >= 24)) | ((month_no == 1) & (day <= 2))
        return result(d, f.astype(float), binary(f, "santa", "rest"))

    if hid in ("triple_witching", "opex_week"):
        # Monthly expiry is the third Friday. Triple witching is the quarterly
        # one; opex week is the whole week containing any monthly expiry.
        day = (d - months).astype(int) + 1
        third_friday = (dow == 4) & (day >= 15) & (day <= 21)
        if hid == "triple_witching":
            f = third_friday & np.isin(month_no, [3, 6, 9, 12])
            return result(d, f.astype(float), binary(f, "witching", "rest"))
        expiries = d[third_friday]
        f = np.zeros(d.shape, dtype=bool)
        for e in expiries:
            f |= (d >= e - np.timedelta64(4, "D")) & (d <= e)
        return result(d, f.astype(float), binary(f, "opex_week", "rest"))

    raise ValueError(f"calendar module has no rule for {hid!r}")
