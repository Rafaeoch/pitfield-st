"""Daily chain capture. The job that makes the archive accrue.

Writes one Parquet partition per underlying per day, in the schema of spec 4.3:

    data/chains/date=YYYY-MM-DD/underlying=TICKER/part.parquet

Point-in-time rules enforced here, not left to convention:

* **Never overwrite.** If a partition exists, a second run writes a *new* file
  beside it with a later snapshot timestamp. Readers take the first observation
  of a given contract on a given day. A correction is a new row, never an edit.
* **Snapshot time is data.** Stored on every row, never inferred from the
  partition key.
* **Version every write.** ``ingest_version`` identifies rows written by a
  given release, so output from a bug can be found without deleting the record
  that it happened.

Exits non-zero on failure so the scheduled run opens an issue. A gap in the
archive is worse than a loud error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

from .calendar_gate import session_close, should_run
from .providers.alpaca import AlpacaNotConfigured, AlpacaProvider

CHAINS_DIR = Path("data/chains")

SCHEMA_ORDER = [
    "snapshot_ts", "quote_ts", "underlying", "expiry", "strike", "right",
    "bid", "ask", "last", "volume", "open_interest", "oi_effective_date",
    "multiplier", "is_adjusted", "spot", "source", "feed", "ingest_version",
    "occ_symbol",
]


def partition_path(asof: date, underlying: str, root: Path = CHAINS_DIR) -> Path:
    return root / f"date={asof}" / f"underlying={underlying.upper()}"


def write_chain(rows: list[dict], asof: date, underlying: str, root: Path = CHAINS_DIR) -> Path:
    """Write one partition. Never overwrites an existing file."""
    if not rows:
        raise ValueError(f"refusing to write an empty chain for {underlying} on {asof}")

    directory = partition_path(asof, underlying, root)
    directory.mkdir(parents=True, exist_ok=True)

    frame = pl.DataFrame(rows)
    for column in SCHEMA_ORDER:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None).alias(column))
    frame = frame.select(SCHEMA_ORDER)

    # A second capture on the same day lands beside the first, timestamped.
    existing = sorted(directory.glob("part*.parquet"))
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    name = "part.parquet" if not existing else f"part.correction-{stamp}.parquet"
    path = directory / name
    frame.write_parquet(path, compression="zstd")
    return path


def already_captured(asof: date, underlying: str, root: Path = CHAINS_DIR) -> bool:
    """Has this session already been written for this underlying?"""
    directory = partition_path(asof, underlying, root)
    return directory.is_dir() and any(directory.glob("part*.parquet"))


def capture(
    underlyings: list[str], asof: date, root: Path = CHAINS_DIR,
    recapture: bool = False,
) -> dict:
    """Capture every underlying. One provider, one pass.

    Skips an underlying whose session is already on disk. This matters because
    the job is scheduled hourly across the afternoon — the market-calendar gate
    only answers "has the close passed", which stays true for the rest of the
    day, so every later firing would otherwise write another snapshot of the
    same session. The archive would gain seven redundant files a day and the
    correction mechanism, which exists for genuine corrections, would become
    noise. Re-capturing is still possible, but it has to be asked for.
    """
    provider = AlpacaProvider()
    results, failures, skipped = [], [], []

    for underlying in underlyings:
        if already_captured(asof, underlying, root) and not recapture:
            skipped.append(underlying)
            print(f"  {underlying:<6} already captured for {asof}; skipping")
            continue
        try:
            rows = provider.fetch_chain(underlying, asof=asof)
            path = write_chain(rows, asof, underlying, root)
            with_oi = sum(1 for r in rows if r.get("open_interest") is not None)
            two_sided = sum(1 for r in rows if r["bid"] > 0 and r["ask"] > 0)
            results.append(
                {
                    "underlying": underlying,
                    "rows": len(rows),
                    "two_sided": two_sided,
                    "with_open_interest": with_oi,
                    "path": str(path),
                }
            )
            print(
                f"  {underlying:<6} {len(rows):>6,} contracts  "
                f"{two_sided:>6,} two-sided  {with_oi:>6,} with OI  -> {path}"
            )
        except Exception as exc:
            failures.append({"underlying": underlying, "error": f"{type(exc).__name__}: {exc}"})
            print(f"  {underlying:<6} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)

    return {
        "date": str(asof),
        "captured": results,
        "failed": failures,
        "skipped": skipped,
        "requests": provider.client.request_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--underlyings", default="SPY", help="comma-separated")
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    parser.add_argument("--root", type=Path, default=CHAINS_DIR)
    parser.add_argument(
        "--force", action="store_true",
        help="skip the market-calendar gate (for a manual catch-up run)",
    )
    parser.add_argument(
        "--recapture", action="store_true",
        help="write a correction even though this session is already archived",
    )
    args = parser.parse_args()

    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))

    if not args.force:
        run, reason = should_run(now_et, minutes_after_close=20)
        if not run:
            # Not an error. Exit clean and quiet on a non-trading day.
            print(f"skipping: {reason}")
            return 0
        print(f"proceeding: {reason}")

    asof = args.date or now_et.date()
    close = session_close(now_et)
    if close is not None:
        print(f"session close {close:%Y-%m-%d %H:%M} ET")

    underlyings = [u.strip().upper() for u in args.underlyings.split(",") if u.strip()]
    print(f"capturing {len(underlyings)} underlying(s) for {asof}")

    try:
        summary = capture(underlyings, asof, args.root, args.recapture)
    except AlpacaNotConfigured as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    print(f"\n{len(summary['captured'])} captured, {len(summary['skipped'])} skipped, "
          f"{len(summary['failed'])} failed, {summary['requests']} API requests")
    # Fail loudly so the scheduled run opens an issue.
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
