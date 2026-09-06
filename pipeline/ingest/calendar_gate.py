"""Decide whether the snapshot job should run right now.

Daylight saving is a real trap and this module exists because of it. A workflow
scheduled at a fixed UTC time drifts by an hour against the New York close twice
a year, so for half the year it would fire *before* the bell and snapshot a live
chain while labelling it as a close. The workflow therefore runs hourly across
the plausible window and asks this module, which resolves the actual session in
exchange-local time.

It also handles the case people forget entirely: half days. The US market closes
at 13:00 ET the day after Thanksgiving, Christmas Eve and a few others, and a
job that waits for 16:00 ET will sit out those sessions or capture them three
hours stale.

Exits 0 either way. A non-trading day is not an error.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("America/New_York")
CALENDAR = "NYSE"


def session_close(now_et: datetime):
    """The scheduled close for ``now_et``'s date, or None if not a trading day.

    Uses the exchange calendar rather than a weekday check, so holidays and half
    days are handled rather than assumed.
    """
    import pandas_market_calendars as mcal

    calendar = mcal.get_calendar(CALENDAR)
    day = now_et.date()
    schedule = calendar.schedule(start_date=day, end_date=day)
    if schedule.empty:
        return None

    # market_close is tz-aware UTC; convert to exchange local time.
    return schedule.iloc[0]["market_close"].tz_convert(EXCHANGE_TZ).to_pydatetime()


def should_run(now_et: datetime, minutes_after_close: int) -> tuple[bool, str]:
    close = session_close(now_et)
    if close is None:
        return False, f"{now_et.date()} is not a trading day"

    ready_at = close + timedelta(minutes=minutes_after_close)
    if now_et < ready_at:
        return False, (
            f"close {close:%H:%M} ET, snapshot due {ready_at:%H:%M} ET, "
            f"now {now_et:%H:%M} ET"
        )

    half_day = close.hour < 15
    label = "half day" if half_day else "full session"
    return True, f"{now_et.date()} {label}, closed {close:%H:%M} ET"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes-after-close", type=int, default=20)
    parser.add_argument(
        "--now", default=None, help="ISO timestamp for testing; defaults to now"
    )
    args = parser.parse_args()

    now_et = (
        datetime.fromisoformat(args.now).astimezone(EXCHANGE_TZ)
        if args.now
        else datetime.now(EXCHANGE_TZ)
    )

    run, reason = should_run(now_et, args.minutes_after_close)
    print(f"should_run={run}: {reason}")

    # GitHub Actions step output.
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"should_run={'true' if run else 'false'}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
