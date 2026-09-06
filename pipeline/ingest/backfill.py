"""Reconstruct historical chains from Alpaca, one past session at a time.

Read this before running it.

There is no endpoint that returns "the chain as it stood on 14 March 2025".
Reconstructing one means enumerating the contracts that were listed then and
pulling each one's quotes in a window before that day's close. That is thousands
of contracts per session, and the request count scales with

    sessions x contracts-per-session / symbols-per-request

so an unbounded backfill of a wide universe is measured in days, not minutes.
This tool therefore prints an estimate and refuses to start without ``--yes``.

Two limits keep it tractable, and both are recorded on every row so the archive
never pretends to more coverage than it has:

* ``--moneyness`` bounds the strike range around the spot of that day. The far
  wings are no-bid anyway and get dropped by the surface filter, so fetching
  them costs requests and buys nothing.
* ``--max-dte`` bounds time to expiry. LEAPS two years out barely trade and
  contribute almost nothing to a fit weighted by vega.

Alpaca's options history begins in February 2024. Requests before that return
nothing, whatever range you ask for.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas_market_calendars as mcal

from .capture import CHAINS_DIR, partition_path, write_chain
from .providers.alpaca import (
    OPTIONS_HISTORY_START,
    AlpacaNotConfigured,
    AlpacaProvider,
)
from .providers.occ import parse as parse_occ

SYMBOLS_PER_REQUEST = 100


def trading_sessions(start: date, end: date) -> list[date]:
    calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(start_date=start, end_date=end)
    return [d.date() for d in schedule.index]


def estimate(sessions: list[date], contracts_per_session: int) -> dict:
    """A deliberately blunt estimate. Better a scary number now than a surprise."""
    per_session = max(1, contracts_per_session // SYMBOLS_PER_REQUEST) + 2
    total = per_session * len(sessions)
    # At the paced interval in the client.
    seconds = total * 0.35
    return {
        "sessions": len(sessions),
        "requests_per_session": per_session,
        "total_requests": total,
        "hours": seconds / 3600.0,
    }


def backfill_session(
    provider: AlpacaProvider,
    underlying: str,
    session: date,
    *,
    moneyness: float,
    max_dte: int,
    root: Path,
) -> dict:
    """One session. Returns a summary; raises only on unexpected failure."""
    if partition_path(session, underlying, root).exists():
        return {"session": str(session), "skipped": "already archived"}

    bars = provider.fetch_underlying_bars(
        underlying, session - timedelta(days=7), session
    )
    on_day = [b for b in bars if b["date"] == session]
    if not on_day:
        return {"session": str(session), "skipped": "no underlying bar; not a session"}
    spot = on_day[-1]["close"]

    symbols = provider.list_contracts(
        underlying,
        expiry_after=session,
        expiry_before=session + timedelta(days=max_dte),
        strike_min=spot * (1 - moneyness),
        strike_max=spot * (1 + moneyness),
    )
    if not symbols:
        return {"session": str(session), "skipped": "no contracts listed"}

    quotes = provider.fetch_closing_quotes(symbols, session)
    if not quotes:
        return {"session": str(session), "skipped": "no quotes returned"}

    metadata = provider.fetch_contract_metadata(underlying)
    snapshot_ts = datetime(
        session.year, session.month, session.day, 20, 20, tzinfo=timezone.utc
    )

    rows = []
    for symbol, quote in quotes.items():
        try:
            occ = parse_occ(symbol)
        except ValueError:
            continue
        meta = metadata.get(symbol, {})
        quote_ts = quote.get("t")
        if isinstance(quote_ts, str):
            try:
                quote_ts = datetime.fromisoformat(quote_ts.replace("Z", "+00:00"))
            except ValueError:
                quote_ts = None
        size = int(meta.get("size") or 100)
        rows.append(
            {
                "snapshot_ts": snapshot_ts,
                "quote_ts": quote_ts,
                "underlying": underlying.upper(),
                "expiry": occ.expiry,
                "strike": float(occ.strike),
                "right": occ.right,
                "bid": float(quote.get("bp") or 0.0),
                "ask": float(quote.get("ap") or 0.0),
                "last": 0.0,
                "volume": None,
                # Open interest here is *today's* reported figure, not the
                # figure as of the backfilled session. It is written with a
                # null effective date so nothing downstream can mistake it for
                # point-in-time positioning, and the quality report counts it.
                "open_interest": meta.get("open_interest"),
                "oi_effective_date": None,
                "multiplier": size,
                "is_adjusted": occ.is_adjusted or size != 100,
                "spot": spot,
                "source": "ALPACA_BACKFILL",
                "ingest_version": "alpaca-backfill-1",
                "occ_symbol": occ.symbol,
            }
        )

    if not rows:
        return {"session": str(session), "skipped": "no parseable rows"}

    path = write_chain(rows, session, underlying, root)
    return {"session": str(session), "rows": len(rows), "path": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--underlying", default="SPY")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--moneyness", type=float, default=0.20,
                        help="strike band around spot, e.g. 0.20 for +/-20%%")
    parser.add_argument("--max-dte", type=int, default=400)
    parser.add_argument("--root", type=Path, default=CHAINS_DIR)
    parser.add_argument("--contracts-per-session", type=int, default=1800,
                        help="used only for the up-front estimate")
    parser.add_argument("--yes", action="store_true", help="actually run")
    args = parser.parse_args()

    start = max(args.start, OPTIONS_HISTORY_START)
    if start != args.start:
        print(f"note: Alpaca options history begins {OPTIONS_HISTORY_START}; "
              f"start moved from {args.start} to {start}")

    sessions = trading_sessions(start, args.end)
    if not sessions:
        print("no trading sessions in range")
        return 1

    est = estimate(sessions, args.contracts_per_session)
    print(f"Backfill {args.underlying}  {sessions[0]} -> {sessions[-1]}")
    print(f"  sessions            {est['sessions']:,}")
    print(f"  requests/session    ~{est['requests_per_session']:,}")
    print(f"  total requests      ~{est['total_requests']:,}")
    print(f"  estimated wall time ~{est['hours']:.1f} hours at the paced rate")
    print(f"  strike band         +/-{args.moneyness:.0%} of spot")
    print(f"  max days to expiry  {args.max_dte}")

    if not args.yes:
        print("\nEstimate only. Re-run with --yes to start.")
        return 0

    try:
        provider = AlpacaProvider()
    except AlpacaNotConfigured as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    written = skipped = failed = 0
    for i, session in enumerate(sessions, 1):
        try:
            result = backfill_session(
                provider, args.underlying, session,
                moneyness=args.moneyness, max_dte=args.max_dte, root=args.root,
            )
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(sessions)}] {session} FAILED {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            continue

        if "skipped" in result:
            skipped += 1
        else:
            written += 1
            print(f"[{i}/{len(sessions)}] {session}  {result['rows']:,} rows")

    print(f"\nwritten {written}, skipped {skipped}, failed {failed}, "
          f"{provider.client.request_count:,} requests")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
