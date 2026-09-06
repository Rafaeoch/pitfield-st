#!/bin/bash
# Daily chain capture. Safe to run many times a day: the market-calendar gate
# inside the job decides whether this is a completed trading session and exits
# cleanly and silently if not. That is why this is scheduled hourly across the
# afternoon rather than at one fixed time — a fixed UTC time drifts an hour
# against the New York close twice a year.
set -uo pipefail
cd "$(dirname "$0")/.."
STAMP=$(date +%Y-%m-%d)
{
  echo "--- $(date '+%Y-%m-%d %H:%M:%S %Z') ---"
  ./.venv/bin/python -m pipeline.ingest.capture --underlyings "${UNDERLYINGS:-SPY}"
  echo "capture exit=$?"

  # Ephemeris exports every day, market open or not. The moon page renders
  # "today", so a stale export shows the wrong phase — and unlike the chain
  # capture this depends on the calendar, not on the exchange being open.
  ./.venv/bin/python -m pipeline.publish.export_celestial --out site/public/data
  echo "celestial exit=$?"
} >> "logs/capture-$STAMP.log" 2>&1
