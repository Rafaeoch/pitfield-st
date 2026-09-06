.PHONY: setup test demo site build clean lint daily ephemeris inputs celestial capture backfill study probe reading notes

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
DAYS ?= 140
UNDERLYINGS ?= SPY
START ?= 2024-09-03
END ?= $(shell date +%Y-%m-%d)

setup:
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet numpy scipy polars pyyaml pyarrow \
		pandas-market-calendars skyfield pytest
	cd site && npm install

test:
	$(PY) -m pytest tests/ -q

## The JPL ephemeris kernel (31MB). Required for anything celestial; the
## astronomy tests skip cleanly without it rather than falling back to an
## approximation.
ephemeris:
	mkdir -p data/ephemeris
	curl -L --fail https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp \
		-o data/ephemeris/de440s.bsp

## Pull every free public input: index history, Kp, sunspots. Cached on disk
## with provenance, so a re-run does not re-hammer the sources.
inputs: ephemeris
	$(PY) -m pipeline.ingest.refresh

## Reproduce a full archive from raw inputs. This is the acceptance criterion:
## a clone plus this target produces the site's data with no manual steps.
demo: celestial
	$(PY) -m pipeline.run_day --days $(DAYS) --out site/public/data

## Ephemeris data for the moon page and the orrery. Needs the kernel.
celestial:
	$(PY) -m pipeline.publish.export_celestial --out site/public/data

## Run the pre-registered celestial study. Real data, no credentials needed.
study: inputs
	$(PY) -m pipeline.study.run_study --resamples 2000 --out site/public/data

## Assemble the reading list from OpenAlex. Free, no key, paced politely.
reading:
	$(PY) -m pipeline.publish.export_literature --out site/public/data

## Generate the per-paper relevance notes. Batched at half price, cached by
## paper, and a no-op without ANTHROPIC_API_KEY.
notes:
	$(PY) -m pipeline.publish.summarize --data site/public/data

## What will Alpaca actually give us? Feed level and earliest history.
probe:
	$(PY) -m pipeline.ingest.probe

## Capture today's real chains from Alpaca. Exits clean on a non-trading day.
## Needs APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment.
capture:
	$(PY) -m pipeline.ingest.capture --underlyings $(UNDERLYINGS)

## Reconstruct historical chains. Prints an estimate; add --yes to run.
## e.g. make backfill START=2024-09-03 ARGS=--yes
backfill:
	$(PY) -m pipeline.ingest.backfill --underlying SPY \
		--start $(START) --end $(END) $(ARGS)

## One trading day, appended to the existing archive. What the cron job runs.
daily:
	$(PY) -m pipeline.run_day --days 1 --out site/public/data

site:
	cd site && npm run dev

build:
	cd site && npm run build

clean:
	rm -rf site/public/data site/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
