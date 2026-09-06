# Pitfield St

A public archive of computed derivatives data, and a standing demonstration of
how easily data of exactly that kind will fool you.

Two halves, one statistical spine.

**The instrument.** Every trading day: ingest option chains, extract forwards and
discount rates from put–call parity, fit arbitrage-free volatility surfaces,
compute a full metric set, publish the results and the quality report describing
what had to be thrown away. The archive grows daily and nothing is deleted or
revised in place.

**The demonstration.** A pre-registered study of roughly thirty celestial and
calendar hypotheses against equity returns, run through the same harness,
corrected for multiple comparisons across the whole family, reported honestly
including the null results.

## Status

| Milestone | State |
|---|---|
| 1. Snapshot job on cron | **capturing real chains**; schedule written, not yet loaded |
| 2. One-page site | done |
| 3. Forward extraction + SVI + arbitrage checks | done, tested |
| 4. Full metrics table | done |
| 5. Per-ticker pages | done (archive scrubber pending) |
| 6. Methodology page | done |
| 7. Quality page | done |
| 8. Pre-registration commit | done — `PREREGISTRATION.md`, frozen before any study code |
| 8b. Free public inputs | **done — real data, no key, no scraping** (see below) |
| 9. Hypothesis engine + harness | **done — 168 tests over 14,010 sessions** |
| 10. Study pages + leaderboard + moon page | done — leaderboard with the Bangladesh line |
| 11. DuckDB-WASM console | not started |
| 12. VRP replication, then one original test | not started |

**The published site still runs on synthetic chains, though the archive no
longer does.** Real capture is working and `data/chains/` is accruing genuine
Alpaca snapshots, but one real session cannot replace 140 synthetic ones without
emptying every history chart on the site. The site switches over once enough
real sessions have accumulated to be worth plotting. Until then every page built
on generated numbers says so at the top, and the QC suite flags the day as
synthetic.

That generator is not only a placeholder. It prices chains from a known forward,
a known discount rate and known SVI parameters, which is what makes the
calibration falsifiable: the tests assert that the pipeline recovers the
parameters it was handed. A calibrator that cannot round-trip is broken, and
without a generator you find that out much later.

## Quick start

```bash
make setup      # virtualenv + dependencies
make ephemeris  # JPL DE440s kernel (31MB), needed for anything celestial
make inputs     # pull every free public input, cached with provenance
make study      # run the pre-registered study (no credentials needed)
make test       # 107 tests: numerics, recovery, arbitrage, astronomy, statistics
make demo       # generate a 140-day archive into site/public/data
make site       # run the site at http://localhost:4321
```

## The inputs that are already real

The celestial half of this project needs no data licence, so it does not have
one. Every input below is free, unauthenticated, and fetched without scraping:

| Input | Source | Coverage |
|---|---|---|
| Index history | FRED `NASDAQCOM` | 14,011 days, 1971– |
| Index history (secondary) | FRED `SP500` | 2,512 days, 2016– |
| Planetary K-index | GFZ Potsdam | 34,580 days, 1932– (768 storm days) |
| Sunspot number | SILSO, Royal Obs. Belgium | 72,967 days, 1818– |
| Ephemeris | JPL DE440s via Skyfield | 1849–2150 |

Payloads are cached on disk with the URL, fetch time and SHA-256 recorded, so
any published result traces to the exact bytes it was computed from.

The astronomy is verified against facts known independently of this code — the
synodic month comes out at 29.53 days, Mercury retrogrades three times in 2026
for 20–24 days each, Mars not at all, and Jupiter and Saturn sit in trine after
their December 2020 conjunction. There is no modulo-29.5 approximation anywhere,
and there must never be: the moon page renders from this same code, so a wrong
terminator would be visible on screen.

The **options** data is the part that still needs a paid, licensed feed.

## Alpaca: what it actually gives us

Measured with `make probe`, not assumed. The findings changed the plan, so they
are recorded here rather than in a commit message.

**Live capture works.** One command pulls a full chain:

```bash
make probe      # feed entitlement and earliest history
make capture    # today's chains; exits clean on a non-trading day
```

A real SPY capture is 12,534 contracts, 11,859 of them two-sided.

**Three constraints, all discovered by asking the API:**

* **This key reaches the `indicative` feed, not OPRA** (403). Indicative quotes
  are derived, not the consolidated NBBO. The feed is detected at runtime,
  warned about on every run, and written onto every row, so no downstream
  number can be ambiguous about what it was built from.
* **There is no historical options *quotes* endpoint.** Not an entitlement
  problem — the route 404s. Historically Alpaca serves only `trades` and
  `bars`, which are *traded prices*, not bid/ask. On a liquid January 2025
  expiry only 68% of near-the-money strikes traded at all on a given day.
  A backfill from that would be fitting surfaces to sparse prints, which is a
  different and much weaker object than the live capture produces. So the
  archive accrues forward rather than backwards.
* **Parity rate extraction fails on the indicative feed.** Every expiry returns
  a negative rate — the call-minus-put line comes out steeper than −1, so the
  implied discount factor exceeds 1. The *forward* from the same regression is
  unaffected and correct, because a forward is a ratio of intercept to slope
  and a scale error largely cancels. The pipeline therefore keeps the extracted
  forward, sanity-tests the extracted rate, and falls back to a stated
  assumption when it fails — labelling every affected expiry
  `rate_source: "assumed"` and recording why in the day's output.

Open interest arrives from `/v2/options/contracts` (the snapshot endpoint has
none) and is present on only a fraction of rows on this feed. It is stored as
null where unknown, never as zero: unknown is not the same as nobody holding
any, and treating it as zero would silently discard most of the chain.

Expired contracts are enumerable, but only under `status=inactive` —
`status=all` returns nothing, which reads exactly like "no data for that date".

### Scheduling the daily capture

```bash
cp scripts/st.pitfield.capture.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/st.pitfield.capture.plist
```

Runs hourly on weekday afternoons; the market-calendar gate decides whether any
given firing is a completed session and exits silently otherwise. To stop it,
`launchctl unload` the same path.

## What the tests actually check

Not that the code runs — that the mathematics is right.

- **Greeks against numerical derivatives of the pricer**, not against a second
  closed form. A closed form copied next to the one under test proves only that
  both came from the same place; differentiating the shipped pricer proves the
  analytic expression is the derivative of the thing we ship.
- **Forward extraction against a known forward**, on chains generated with a
  dividend yield so that spot and the forward genuinely differ. The rate's
  precision degrades as `1/T` at the short end; that behaviour is pinned by a
  test rather than hidden.
- **SVI calibration against a known surface**, compared in volatility points on
  the fitted range. Raw SVI has real parameter degeneracy, so asserting
  parameter-by-parameter recovery would test the optimiser, not the fit.
- **Realized volatility against a simulated intraday path**, where the range
  estimators are held to the session volatility they actually estimate and
  Yang–Zhang to the total — because the range estimators are structurally blind
  to the overnight gap.
- **Arbitrage conditions in both directions**: a clean surface passes, and a
  deliberately arbitrageable one is detected.

## Layout

```
pipeline/
  ingest/     chain snapshots, synthetic generator, ephemeris
  compute/    forward, black76, svi, greeks, realized_vol, aggregates, metrics
  study/      pre-registered hypothesis engine (not yet built)
  quality/    daily QC suite
  publish/    site export
  run_day.py  the spine: chain -> forward -> IV -> SVI -> arbitrage -> metrics
site/         Astro static site
  pages/moon/   everything celestial: the render, the history scrubber, the study
tests/        the above
```

## Non-goals

Permanently out of scope, not "later": user accounts, trading signals, alerts or
screeners, backtest equity curves, a server or database, redistribution of raw
real-time quotes, and any claim of predictive edge.

## Known deviations from the specification

- **Typeface.** The design calls for Iosevka for numeric columns. Iosevka is not
  served by Google Fonts, so IBM Plex Mono stands in until Iosevka is
  self-hosted. Literata is as specified.
- **SSVI.** Expiries are currently fitted independently. The global SSVI fit
  across maturities, which would enforce the calendar condition by construction
  rather than checking it afterwards, is specified but not yet built.
- **Storage.** Output is JSON for the site. The partitioned Parquet lake and
  object storage are scaffolded but not wired up.
- **Return series.** The study uses the NASDAQ Composite from 1971 rather than
  the S&P 500 from 1960. No free, licence-clean, key-free daily S&P 500 series
  going back that far could be found. Recorded as deviation D1 in
  `PREREGISTRATION.md`, before any study code was written.

## Licence and data

Market data will be sourced under a single-person, non-professional,
non-redistributing licence. Published quotes will therefore be end-of-day or
delayed, never streaming. Derived values — implied volatilities, greeks, SVI
parameters, aggregates — are this project's own computations.

Not investment advice. No claim of predictive value.
