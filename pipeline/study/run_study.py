"""Run the pre-registered study end to end and write the results.

Order of operations, which matters:

  1. Load the frozen registry.
  2. Compute every variable over the session dates.
  3. Test every bucket of every hypothesis against every return series.
  4. Correct across the *whole* family at once.
  5. Write everything, including the nulls and the hypotheses that could not
     be run at all.

Nothing is dropped for being uninteresting, and nothing is added for being
interesting.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..ingest.market_history import PRIMARY_SERIES, SERIES, load_series
from .harness import DEFAULT_RESAMPLES, apply_corrections, evaluate_bucket
from .registry import NOT_IMPLEMENTED, load, variable_module

HORIZONS = {"IDX_daily": 0, "IDX_fwd_5d": 5, "IDX_fwd_21d": 21}

# Nominal period of each cyclical hypothesis, in years. Used only for
# reporting: a hypothesis whose cycle completes two or three times in the whole
# sample has almost no independent observations of that cycle, whatever the
# number of daily rows suggests. Bucketing it into terciles then partitions
# history into three long eras and asks whether they differ, which they always
# do. Recording the count is what lets a reader see that for themselves rather
# than reading a p-value at face value.
#
# This is a reporting annotation, not a change to any pre-registered test. The
# frozen registry is untouched.
CYCLE_PERIOD_YEARS = {
    "metonic_cycle": 19.0,
    "saros_cycle": 18.03,
    "lunar_nodes": 18.6,
    "saturn_return": 29.5,
    "jupiter_saturn_conjunction": 19.86,
    "sunspot_number": 11.0,
    "lunar_perigee": 0.0755,        # anomalistic month
    "lunar_phase": 0.0808,          # synodic month
}


def build_returns(series_id: str) -> tuple[np.ndarray, dict]:
    """Session dates plus each return series, aligned to those dates."""
    px = load_series(series_id)
    dates = px.return_dates                       # aligned to log_returns
    out = {"IDX_daily": px.log_returns}
    for name, horizon in HORIZONS.items():
        if horizon == 0:
            continue
        fwd_dates, fwd = px.forward_return(horizon)
        # Align the forward window onto the daily return dates, leaving the
        # tail NaN rather than padding it. Padding would be lookahead.
        lookup = dict(zip(fwd_dates.tolist(), fwd.tolist()))
        out[name] = np.array([lookup.get(d, np.nan) for d in dates.tolist()])
    return dates, out


def run(series_id: str = PRIMARY_SERIES, resamples: int = DEFAULT_RESAMPLES) -> dict:
    registry = load()
    dates, returns = build_returns(series_id)
    px = load_series(series_id)

    results, skipped = [], []
    for spec in registry["hypotheses"]:
        hid = spec["id"]
        module = variable_module(spec)
        if module is None:
            skipped.append({"id": hid, "name": spec["name"], "tier": spec["tier"],
                            "reason": NOT_IMPLEMENTED[hid]})
            continue

        try:
            computed = module.compute(dates, spec)
        except Exception as exc:
            skipped.append({"id": hid, "name": spec["name"], "tier": spec["tier"],
                            "reason": f"variable failed: {type(exc).__name__}: {exc}"})
            continue

        buckets = computed["bucket"]
        labels = [b for b in sorted(set(buckets.tolist())) if b != "unknown"]
        # A two-bucket variable yields one contrast, not two mirror images of
        # the same one. Testing both would double-count it in the correction.
        if len(labels) == 2:
            labels = labels[:1]

        for series_name in spec["return_series"]:
            y = returns.get(series_name)
            if y is None:
                continue
            for label in labels:
                results.append(
                    evaluate_bucket(hid, series_name, label, y,
                                    buckets == label, resamples=resamples)
                )
        print(f"  {hid:<28} {len(labels)} bucket(s) x {len(spec['return_series'])} series")

    tested = [r for r in results if np.isfinite(r.p_value)]
    apply_corrections(tested)

    span_years = (dates[-1] - dates[0]).astype(int) / 365.25
    cycles = {hid: round(span_years / period, 2)
              for hid, period in CYCLE_PERIOD_YEARS.items()}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "series": {"id": series_id, "label": SERIES[series_id][0],
                   "n_sessions": int(dates.size),
                   "start": str(dates[0]), "end": str(dates[-1]),
                   "source_url": px.source_url, "sha256": px.sha256},
        "n_tests": len(tested),
        "n_hypotheses_run": len({r.hypothesis for r in tested}),
        "n_hypotheses_skipped": len(skipped),
        "bootstrap_resamples": resamples,
        "sample_span_years": round(span_years, 1),
        "cycles_in_sample": cycles,
        "results": [r.as_dict() for r in tested],
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", default=PRIMARY_SERIES, choices=sorted(SERIES))
    parser.add_argument("--resamples", type=int, default=2000)
    parser.add_argument("--out", type=Path, default=Path("site/public/data"))
    args = parser.parse_args()

    print(f"Running the pre-registered study on {args.series}")
    payload = run(args.series, args.resamples)

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "study.json"
    path.write_text(json.dumps(payload, separators=(",", ":")))

    rows = payload["results"]
    raw = sum(1 for r in rows if r["p_value"] is not None and r["p_value"] < 0.05)
    bh = sum(1 for r in rows if r["p_bh"] is not None and r["p_bh"] < 0.10)
    bonf = sum(1 for r in rows if r["p_bonferroni"] is not None and r["p_bonferroni"] < 0.05)

    print(f"\n{payload['series']['n_sessions']:,} sessions, "
          f"{payload['series']['start']} to {payload['series']['end']}")
    print(f"{payload['n_tests']} tests across {payload['n_hypotheses_run']} hypotheses"
          f"  ({payload['n_hypotheses_skipped']} not run)")
    print(f"\n  significant, uncorrected  p < 0.05 : {raw}")
    print(f"  survive Benjamini-Hochberg q < 0.10 : {bh}")
    print(f"  survive Bonferroni         p < 0.05 : {bonf}")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
