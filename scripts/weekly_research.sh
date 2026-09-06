#!/bin/bash
# Weekly research refresh: the pre-registered study and the reading list.
#
# Weekly, not daily, and for different reasons in each case. The study's inputs
# are decades long, so one more session moves nothing — re-running it daily
# would burn an hour of bootstrap resampling to change the fourth decimal. The
# reading list changes when the literature does, which is not on a daily
# cadence either.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
STAMP=$(date +%Y-%m-%d)
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  ./.venv/bin/python -m pipeline.publish.export_literature --out site/public/data
  echo "reading exit=$?"

  # Generate notes for any newly surfaced papers. Cached, so this is free
  # unless the reading list actually changed, and a no-op without a key.
  ./.venv/bin/python -m pipeline.publish.summarize --data site/public/data
  echo "notes exit=$?"
  ./.venv/bin/python -m pipeline.study.run_study --resamples 2000 --out site/public/data
  echo "study exit=$?"

  # Rebuild: pages read the archive at build time. Without this the
  # published numbers stop moving while the globe keeps turning.
  ./scripts/publish_site.sh
  echo "publish exit=$?"
} >> "logs/weekly-$STAMP.log" 2>&1
