"""Assemble the site's index from the per-day archive.

Run after the daily job. It re-reads the archive rather than trusting anything
held in memory, which means the published index is always a function of the
files on disk — the same files a reader can download.

The one computation that happens here rather than in the daily job is the
variance risk premium, because it cannot be computed on the day it applies to:

    VRP(t) = IV30(t) - RV30(t -> t+30)

The realized leg is not known until thirty days after t. It is therefore
published against the date it describes, but only once that window has actually
closed, and every row carries the date its realized leg was measured on. A
forward-looking VRP presented as though it were known on the day is a lookahead
bug wearing a metric's clothes, and it is an easy one to ship by accident.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

# Trading days spanned by a 30-calendar-day window.
VRP_LAG_TRADING_DAYS = 21

HISTORY_FIELDS = (
    ("iv_30", "metrics"),
    ("iv_60", "metrics"),
    ("iv_90", "metrics"),
    ("rv_cc_21", "metrics"),
    ("rv_yz_21", "metrics"),
    ("rv_park_21", "metrics"),
    ("rv_park_cc_ratio_21", "metrics"),
    ("risk_reversal_25d", "metrics"),
    ("butterfly_25d", "metrics"),
    ("term_slope_90_30", "metrics"),
    ("iv_rank", "metrics"),
    ("gex_total", "aggregates"),
    ("gamma_flip_strike", "aggregates"),
    ("crossed_share", "quality"),
    ("zero_bid_share", "quality"),
    ("qc_passed", "quality"),
    ("excluded_share", "coverage"),
    ("worst_min_g", "arbitrage"),
    ("calendar_ok", "arbitrage"),
    ("n_expiries_fitted", "arbitrage"),
)


def load_archive(archive_dir: Path) -> list[dict]:
    """Every archived day, in date order."""
    records = []
    for path in sorted(archive_dir.glob("*.json")):
        try:
            records.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            print(f"  ! skipping unreadable {path.name}")
    records.sort(key=lambda r: r["date"])
    return records


def build_history(records: list[dict]) -> list[dict]:
    rows = []
    for i, record in enumerate(records):
        row = {"date": record["date"], "spot": record.get("spot")}
        for field, section in HISTORY_FIELDS:
            row[field] = record.get(section, {}).get(field)

        # Variance risk premium, only where the realized window has closed.
        forward_index = i + VRP_LAG_TRADING_DAYS
        iv30 = record.get("metrics", {}).get("iv_30")
        if forward_index < len(records) and iv30 is not None:
            realized = records[forward_index].get("metrics", {}).get("rv_cc_21")
            if realized is not None:
                row["vrp_30"] = round(iv30 - realized, 8)
                row["vrp_realized_on"] = records[forward_index]["date"]
            else:
                row["vrp_30"] = None
                row["vrp_realized_on"] = None
        else:
            # Not yet computable. Explicitly null rather than absent, so the
            # site renders "pending" instead of silently dropping the row.
            row["vrp_30"] = None
            row["vrp_realized_on"] = None
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("site/public/data"))
    parser.add_argument(
        "--target",
        choices=["local"],
        default="local",
        help="only 'local' is implemented; R2 activates with the licensed feed",
    )
    args = parser.parse_args()

    archive_dir = args.data / "archive"
    if not archive_dir.is_dir():
        print(f"no archive at {archive_dir}")
        return 1

    records = load_archive(archive_dir)
    if not records:
        print("archive is empty")
        return 1

    latest = records[-1]
    history = build_history(records)

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": latest.get("pipeline_version"),
        "synthetic": any(r.get("synthetic") for r in records),
        "underlying": latest.get("underlying"),
        "latest_date": latest["date"],
        "n_days": len(records),
        "dates": [r["date"] for r in records],
        "contract_days": sum(
            r.get("coverage", {}).get("n_chain_rows", 0) for r in records
        ),
        "vrp_lag_trading_days": VRP_LAG_TRADING_DAYS,
        "history": history,
    }

    (args.data / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    (args.data / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))

    computable = sum(1 for row in history if row["vrp_30"] is not None)
    print(f"index: {len(records)} days, {index['contract_days']:,} contract-days")
    print(f"VRP computable on {computable}/{len(history)} days "
          f"(the last {VRP_LAG_TRADING_DAYS} cannot be, by construction)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
