#!/usr/bin/env bash
# Server-side half of a deploy. Runs as the pitfield user, from /opt/pitfield.
# Invoked by deploy/deploy.sh; also fine to run by hand on the box.
#
# This is a file rather than a heredoc piped into `ssh ... bash -s` on purpose:
# nested heredocs and a script read from stdin compete for the same stdin, and
# the failure mode is silent truncation of whichever one loses.
set -euo pipefail
cd /opt/pitfield

export PITFIELD_WWW="${PITFIELD_WWW:-/var/www/pitfield}"
set -a; . /etc/pitfield/env 2>/dev/null || true; set +a

if [ ! -x .venv/bin/python ]; then
  echo "-- creating venv (Linux; the laptop's is never copied)"
  python3 -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet numpy scipy polars pyyaml pyarrow \
  pandas-market-calendars skyfield pytest

if [ ! -f data/ephemeris/de440s.bsp ]; then
  echo "-- fetching JPL DE440s kernel (31MB, once)"
  mkdir -p data/ephemeris
  curl -fsSL --retry 3 \
    https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp \
    -o data/ephemeris/de440s.bsp
fi

echo "-- public inputs (cached on disk; a re-run does not re-hammer the sources)"
./.venv/bin/python -m pipeline.ingest.refresh

echo "-- node deps from the lockfile"
( cd site && npm ci --silent --no-audit --no-fund )

echo "-- tests, on the server's own Python"
./.venv/bin/python -m pytest tests/ -q

# Seed only if this box has never published. Synthetic until real captures
# accrue, and the banner on every page says so until they do.
if [ ! -f site/public/data/index.json ]; then
  echo "-- seeding a first archive"
  ./.venv/bin/python -m pipeline.run_day --days 140 --out site/public/data
  ./.venv/bin/python -m pipeline.publish.export_celestial  --out site/public/data
  ./.venv/bin/python -m pipeline.publish.export_literature --out site/public/data
  ./.venv/bin/python -m pipeline.study.run_study --resamples 2000 --out site/public/data
fi

./scripts/publish_site.sh
