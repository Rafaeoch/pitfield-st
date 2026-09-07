"""Find out what Alpaca will actually give us, rather than assuming.

Three questions that decide the shape of the archive:

  1. Do the credentials work, and which options feed do they reach? OPRA is the
     real NBBO; "indicative" is a free derived feed. That distinction matters
     more than history length — a surface fitted to an indicative quote is a
     surface fitted to an estimate of a quote.
  2. What contracts actually existed in the past? Asked of the API rather than
     constructed by hand: guessing OCC symbols means guessing expiry calendars,
     and a wrong guess looks exactly like missing data.
  3. How far back does options history really go?
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

from .providers.alpaca import (
    DATA_BASE,
    AlpacaClient,
    AlpacaNotConfigured,
    AlpacaProvider,
)


def check_feed(client: AlpacaClient, feed: str) -> tuple[bool, str]:
    """Can this key read the given options feed?"""
    try:
        payload = client.get(
            f"{DATA_BASE}/v1beta1/options/snapshots/SPY", {"feed": feed, "limit": 1}
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:90]}"
    n = len(payload.get("snapshots") or {})
    return n > 0, f"{n} snapshot(s)"


def real_contract(client: AlpacaClient, expiry_year: int, expiry_month: int) -> str | None:
    """Ask the API for a contract that expired in that month. No guessing."""
    first = date(expiry_year, expiry_month, 1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    try:
        payload = client.get(
            f"{client.trading_base}/v2/options/contracts",
            {
                "underlying_symbols": "SPY",
                "expiration_date_gte": first.isoformat(),
                "expiration_date_lte": last.isoformat(),
                "status": "all",
                "limit": 60,
            },
        )
    except Exception:
        return None
    contracts = payload.get("option_contracts") or []
    if not contracts:
        return None
    # Prefer something near the money for that era so it actually traded.
    calls = [c for c in contracts if c.get("type") == "call"] or contracts
    calls.sort(key=lambda c: abs(float(c.get("strike_price", 0)) - 500))
    return calls[0]["symbol"]


def has_quotes(client: AlpacaClient, symbol: str, day: date, feed: str) -> bool:
    start = datetime(day.year, day.month, day.day, 15, 0, tzinfo=timezone.utc)
    try:
        payload = client.get(
            f"{DATA_BASE}/v1beta1/options/quotes",
            {
                "symbols": symbol,
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": (start + timedelta(hours=5)).isoformat().replace("+00:00", "Z"),
                "limit": 1,
                "feed": feed,
            },
        )
    except Exception:
        return False
    return bool(payload.get("quotes"))


def main() -> int:
    print("Probing Alpaca\n")
    try:
        provider = AlpacaProvider()
    except AlpacaNotConfigured as exc:
        print(exc, file=sys.stderr)
        return 2
    client = provider.client

    print(f"  credentials      accepted")
    print(f"  SPY spot         {provider.fetch_spot('SPY')}")

    print("\n  options feed entitlement:")
    usable = None
    for feed in ("opra", "indicative"):
        ok, detail = check_feed(client, feed)
        print(f"    {feed:<12} {'YES' if ok else 'no ':<4} {detail}")
        if ok and usable is None:
            usable = feed
    if usable is None:
        print("\n  No options feed is readable with this key.")
        return 1
    print(f"\n  using {usable!r}")
    if usable == "indicative":
        print("    ^ derived, not real NBBO. Fine for wiring up; not sound to")
        print("      publish a volatility surface from.")

    print("\n  earliest options history (real contracts, asked not guessed):")
    earliest = None
    for year in (2024, 2025):
        for month in (1, 4, 7, 10):
            probe_day = date(year, month, 15)
            if probe_day >= date.today():
                break
            # A contract expiring ~6 months after the probe date.
            exp_month = month + 6
            exp_year = year + (1 if exp_month > 12 else 0)
            exp_month = exp_month - 12 if exp_month > 12 else exp_month
            symbol = real_contract(client, exp_year, exp_month)
            if symbol is None:
                print(f"    {year}-{month:02d}  no contracts listed for {exp_year}-{exp_month:02d}")
                continue
            ok = has_quotes(client, symbol, probe_day, usable)
            print(f"    {year}-{month:02d}  {'DATA' if ok else '—   '}  via {symbol}")
            if ok and earliest is None:
                earliest = (year, month)

    print()
    if earliest:
        y, m = earliest
        print(f"  earliest quarter with data: {y}-{m:02d}")
        if (y, m) <= (2024, 1):
            print("  -> a clean calendar year from 2024-01-01 is available")
        else:
            print(f"  -> 2024-01 not reachable. Clean year: 2025-01-01 onward.")
    else:
        print("  no options quotes found at any probed date")
    print(f"\n  {client.request_count} requests used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
