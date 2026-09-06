# Pitfield St — Build Specification

**Name.** Named for the street, in the convention of institutions that take their name from an address rather than a claim — Threadneedle, Bishopsgate, Grosvenor. It reads as a research house rather than a signals service, which is the point. The constraint that produced it still holds for anything named later: no *alpha, edge, signals, profit,* or *whale.*

**Version:** 1.0
**Audience:** the coding agent building this
**Read this section first:** [Non-Goals](#0-non-goals) — it prevents most of the ways this goes wrong.

---

## 0. Non-Goals

Do not build any of the following. They are out of scope permanently, not "later."

- No user accounts, login, email capture, paywall, or newsletter signup
- No trading signals, buy/sell recommendations, alerts, or screeners
- No backtest equity curves. Not one.
- No server, database, or API backend. Everything is static files on a CDN.
- No redistribution of raw real-time quotes (licensing — see §1.1)
- No claims of predictive edge anywhere in the copy

If a feature request sounds like it belongs on a retail trading product, it does not belong here.

---

## 1. What This Is

A public research archive with two halves that share one statistical spine.

**Half one — the instrument.** Every trading day after the close, ingest options chains for a universe of liquid underlyings, fit arbitrage-free volatility surfaces, compute a full metric set, and publish the results plus the raw data. The archive grows daily and never gets deleted.

**Half two — the demonstration.** A pre-registered study testing ~30 celestial and calendar hypotheses against equity returns, run through the same statistical harness, corrected for multiple comparisons across the whole family, reported honestly including null results.

The thesis, stated on the homepage: here is rigorously computed derivatives data, and here is a standing demonstration of how easily this exact kind of data will fool you.

### 1.1 Licensing constraints (do not violate)

Data is sourced under a single-person, non-professional, non-redistributing license.

- Published quotes must be end-of-day or delayed, never real-time streaming.
- Published derived values (IV, greeks, SVI parameters, aggregates) are our own computations and are fine to publish.
- Do not add a real-time feed, a websocket, or an "unusual activity" tape.

---

## 2. Stack

| Layer | Choice | Why |
|---|---|---|
| Pipeline | Python 3.12 | ecosystem for the numerics |
| Dataframes | Polars | lazy scans over a growing Parquet lake |
| Numerics | NumPy, SciPy | SVI calibration, statistics |
| Ephemeris | Skyfield + DE440s | real astronomy, not modulo arithmetic |
| Calendar | pandas-market-calendars | holidays, half days, DST |
| Storage | Parquet (zstd), partitioned | columnar, CDN-servable |
| Site | Astro (static output) | MDX for methodology, islands for interactivity |
| Charts | Observable Plot | small, declarative, good defaults |
| 3D | three.js | vol surface + moon render |
| In-browser SQL | DuckDB-WASM | query the archive with no backend |
| Scheduling | GitHub Actions (cron) | free, auditable, public run logs |
| Hosting | Cloudflare Pages + R2 | static site, object storage for Parquet |

Public Actions logs are a feature, not an implementation detail — they are third-party evidence the pipeline has been running unattended. Keep the repo public.

---

## 3. Repository Layout

```
pitfield-st/
├── pipeline/
│   ├── ingest/
│   │   ├── chains.py            # daily options chain snapshot
│   │   ├── underlying.py        # OHLCV for underlyings
│   │   ├── corporate_actions.py # splits, special dividends
│   │   └── celestial.py         # ephemeris + Kp index ingest
│   ├── compute/
│   │   ├── forward.py           # put-call parity forward & rate extraction
│   │   ├── svi.py               # SVI calibration + arbitrage checks
│   │   ├── greeks.py            # Black-76 greeks incl. 2nd order
│   │   ├── realized_vol.py      # close-to-close + range estimators
│   │   ├── aggregates.py        # GEX, flip point, P/C ratios
│   │   └── metrics.py           # daily metric table assembly
│   ├── study/
│   │   ├── hypotheses.yaml      # PRE-REGISTERED. See §6.1
│   │   ├── registry.py          # loads + validates hypotheses.yaml
│   │   ├── variables/           # one module per celestial variable
│   │   ├── harness.py           # bootstrap, HAC, FDR, power
│   │   └── run_study.py
│   ├── quality/
│   │   └── checks.py            # QC suite, emits a daily report
│   └── publish/
│       └── export.py            # writes JSON + Parquet for the site
├── data/                        # gitignored; lives in R2
├── site/
│   ├── src/pages/
│   ├── src/components/
│   ├── src/content/methodology/ # MDX
│   └── public/
├── .github/workflows/daily.yml
├── PREREGISTRATION.md           # frozen; see §6.1
└── README.md
```

---

## 4. Data Layer

### 4.1 Universe

Start with ~150 underlyings: SPY, QQQ, IWM, DIA, plus the most liquid single names by options volume, plus VIX. Store the universe in `config/universe.yaml` with an `added_date` per ticker so the universe itself is point-in-time reconstructable. Never remove a ticker — mark it `retired_date` instead. Removing tickers creates survivorship bias in every downstream study.

### 4.2 Snapshot job

Runs once per trading day, 20 minutes after the close.

DST is a real trap. Do not hardcode a UTC cron and assume it lands after the close. Schedule the Action hourly in the relevant window and have the job itself check `pandas_market_calendars` for whether today was a trading day and whether the close has passed. Exit cleanly and silently on non-trading days. Handle half days (1:00 PM ET closes).

For each underlying capture: full chain across all listed expiries — strike, expiry, right, bid, ask, last, volume, open interest, and the exchange timestamp if available. Plus the underlying's OHLCV bar.

### 4.3 Schema

`data/chains/date=YYYY-MM-DD/underlying=TICKER/part.parquet`

| column | type | notes |
|---|---|---|
| snapshot_ts | timestamp[us, UTC] | when we captured it |
| quote_ts | timestamp[us, UTC] | exchange timestamp, null if unavailable |
| underlying | string | |
| expiry | date | |
| strike | float64 | |
| right | string | C / P |
| bid, ask, last | float64 | |
| volume, open_interest | int64 | OI is previous-day settled — see below |
| multiplier | int32 | 100 normally; differs for adjusted contracts |
| is_adjusted | bool | non-standard deliverable |
| spot | float64 | underlying price at snapshot |
| ingest_version | string | code version that wrote this row |

`data/metrics/date=YYYY-MM-DD/part.parquet` — one row per underlying per day, all of §5.
`data/surfaces/date=YYYY-MM-DD/underlying=TICKER.parquet` — fitted SVI params per expiry.

### 4.4 Point-in-time rules

These are the rules a quant reviewer will check first.

- **Never overwrite.** Corrections are written as new rows with a later `snapshot_ts`. Readers default to the first observation of a given (date, underlying, expiry, strike, right).
- **Open interest lags.** OI reported today reflects yesterday's settled positions. Every downstream metric using OI must be timestamped to the OI's effective date, not the snapshot date. Document this on the methodology page.
- **Snapshot time is data.** Store it. Never infer it from the partition key.
- **Version every write.** `ingest_version` lets you identify which rows came from buggy code without deleting anything.

### 4.5 Corporate actions

Splits change both strikes and multipliers, and leave behind adjusted contracts with non-standard deliverables. Handle this explicitly:

- Maintain `data/corporate_actions.parquet` with ticker, effective date, and split ratio.
- Flag `is_adjusted=true` for any contract whose multiplier ≠ 100 or whose strike is not on the standard increment grid.
- Exclude adjusted contracts from surface fits. They price off a different deliverable and will wreck a calibration silently.
- Never back-adjust stored raw data. Apply adjustments at read time.

### 4.6 Quality checks

Run daily, write `data/quality/date=YYYY-MM-DD.json`, and publish it (§7). Track:

- Crossed markets (bid > ask) — count and worst offenders
- Locked markets (bid == ask)
- Zero-bid contracts as a share of chain
- Stale quotes (quote_ts older than N minutes before close)
- Missing expiries versus the exchange's listed cycle
- Strike grid gaps
- Chains that failed to fit (§5.2) and why
- Row count deltas versus the trailing 20-day median — a sudden drop means a broken source

Log and flag; never silently drop. The share of the chain excluded from each fit is itself a published number.

---

## 5. Compute Layer

### 5.1 Forward and discount rate extraction

Do not plug in a Treasury yield and a dividend estimate. Back both out of the chain.

For each expiry, using strikes with two-sided markets on both call and put, regress the call-minus-put mid price on strike:

```
C(K) − P(K) = e^(−rT)·F − e^(−rT)·K
```

This is linear in K with slope β = −e^(−rT) and intercept α = e^(−rT)·F. Therefore:

```
r = −ln(−β) / T
F = −α / β
```

Use only strikes within roughly 0.85–1.15 moneyness where both legs are liquid. Record the regression R² per expiry and reject the expiry if it falls below 0.99 — a poor fit means bad quotes, not a real forward.

This implicitly captures dividends and borrow cost, which is exactly why it beats plugging in a rate. Say so on the methodology page. It is a small detail that signals you have done this before.

### 5.2 SVI calibration

Work in total implied variance `w = σ²·T` against log-moneyness `k = ln(K/F)`.

Raw SVI (Gatheral):

```
w(k) = a + b·( ρ·(k − m) + sqrt( (k − m)² + σ² ) )
```

Parameter constraints:

```
b ≥ 0,  |ρ| < 1,  σ > 0,  a + b·σ·sqrt(1 − ρ²) ≥ 0
```

Fitting procedure:

1. Filter: drop zero-bid, drop is_adjusted, drop crossed, keep |k| < 1.5, require open_interest > 0.
2. Invert mid prices to IV with Black-76 using the extracted F and r. Use Brent on a bracketed interval; if inversion fails, drop the contract and log it.
3. Weight by vega, or by inverse relative spread. Do not weight equally — equal weighting lets illiquid wings dominate a fit that matters most at the money.
4. Calibrate per expiry via least squares with the constraints above (SLSQP or differential evolution seeded from the previous day's parameters).
5. Then fit SSVI across expiries for a globally arbitrage-free surface.

### 5.3 Arbitrage checks — mandatory, and published

**Butterfly (Durrleman's condition).** For each expiry, on a dense k grid:

```
g(k) = (1 − k·w'(k)/(2·w(k)))²  −  (w'(k)²/4)·(1/w(k) + 1/4)  +  w''(k)/2
```

Require g(k) ≥ 0 everywhere. g(k) < 0 implies negative implied probability density.

**Calendar.** Total variance w(k, T) must be non-decreasing in T at fixed k. Check on a grid across all fitted expiries.

Store per-surface: `butterfly_ok` (bool), `min_g` (float), `calendar_ok` (bool), `n_calendar_violations` (int), `rmse_vol_points` (float), `n_contracts_used`, `n_contracts_dropped`.

Publish the violations. A page showing a surface with negative implied density, next to the constrained fit, is worth more than a page of prose about why arbitrage constraints matter.

### 5.4 Greeks

Use Black-76 on the extracted forward throughout. This sidesteps the dividend problem entirely because dividends are already in F.

```
d1 = ( ln(F/K) + σ²T/2 ) / ( σ·sqrt(T) )
d2 = d1 − σ·sqrt(T)
```

First order (call; put by parity):

```
Delta  = e^(−rT)·N(d1)
Gamma  = e^(−rT)·φ(d1) / ( F·σ·sqrt(T) )
Vega   = e^(−rT)·F·φ(d1)·sqrt(T)
```

Second order:

```
Vanna  = −e^(−rT)·φ(d1)·d2 / σ
Volga  = Vega·d1·d2 / σ
```

Theta and Charm: finite-difference with a one-day bump, and document the bump size on the methodology page. Closed forms exist but the bump is easier to verify and the honesty reads well.

Report Vega per volatility point (divide by 100) and Theta per calendar day. State the convention — half of all greek disagreements are unit disagreements.

### 5.5 Realized volatility

Compute at horizons 5, 10, 21, 63, 252 days. All annualized with 252 trading days.

**Close-to-close:** standard deviation of log returns.

**Parkinson (high-low):**

```
σ²_P = (1 / (4·N·ln2)) · Σ [ ln(H_i / L_i) ]²
```

**Garman–Klass:**

```
σ²_GK = (1/N) · Σ { 0.5·[ln(H_i/L_i)]² − (2·ln2 − 1)·[ln(C_i/O_i)]² }
```

**Rogers–Satchell (drift-independent):**

```
σ²_RS = (1/N) · Σ { ln(H_i/C_i)·ln(H_i/O_i) + ln(L_i/C_i)·ln(L_i/O_i) }
```

**Yang–Zhang (handles overnight gaps):**

```
σ²_YZ = σ²_overnight + k·σ²_open_to_close + (1 − k)·σ²_RS
k = 0.34 / ( 1.34 + (N+1)/(N−1) )
```

where σ²_overnight uses ln(O_i / C_{i−1}).

Also publish σ_P / σ_CC — the ratio of range-based to close-to-close vol. It reads as an intraday-range indicator and essentially nobody publishes it.

### 5.6 Implied vol metrics

- Constant-maturity ATM IV at 30/60/90 days. Interpolate in total variance, then convert back to IV. Interpolating IV directly is wrong and is a common tell.
- IV rank = (IV30 − min252) / (max252 − min252)
- IV percentile = share of trailing 252 days with IV30 below today's
- Term structure slope = IV90 − IV30, plus a contango/backwardation flag
- Variance risk premium = IV30(t) − RV30(t → t+30). Only computable with a 30-day lag — publish it lagged and label it clearly. Never present a forward-looking VRP as if it were known.

### 5.7 Skew

- 25-delta risk reversal: IV(25Δ put) − IV(25Δ call)
- 25-delta butterfly: (IV(25Δ put) + IV(25Δ call))/2 − IV(ATM)
- ATM skew slope: ∂σ/∂k evaluated at k = 0 from the fitted SVI
- Percentile rank of each against that ticker's own 252-day history

### 5.8 Chain aggregates

Gamma exposure by strike:

```
GEX(K) = Γ(K) · OI(K) · multiplier · S² · 0.01
```

Dollar gamma per 1% move in spot.

The sign convention is an assumption, not a fact. The common convention treats dealers as long gamma from calls and short from puts. This assumes customers buy puts and sell calls, which is a generalization. Implement it as a config flag with the assumption named in the output schema, and state it prominently on both the methodology page and any page displaying GEX. Flagging this assumption is a stronger credibility signal than the number itself.

Also compute: gamma flip point (where cumulative GEX crosses zero), max-gamma strike, max-OI strike, put/call ratio by volume and by OI, and volume-to-OI ratio (values > 1 indicate new positioning rather than closing).

---

## 6. Celestial Hypothesis Engine

### 6.1 Pre-registration — do this before writing any test code

Create `PREREGISTRATION.md` containing the complete hypothesis list, the return series, the bucketing rules, the test statistic, and the correction method. Commit it. Record the commit hash and timestamp. Link that commit from the results page.

Then write the test code. The order matters and is the entire point: it makes the multiple-comparisons correction honest rather than retrofitted.

`hypotheses.yaml` is frozen at that commit. Adding a hypothesis later requires a new pre-registration commit, a new family, and separate reporting. Do not quietly append.

### 6.2 Registry format

```yaml
- id: lunar_phase
  name: Lunar phase
  family: celestial
  prior_plausibility: none          # mechanism | weak | none
  source: "Yuan & Zheng, Are Investors Moonstruck?"
  variable_module: variables.lunar_phase
  bucketing: {type: categorical, buckets: [new, waxing, full, waning]}
  return_series: [SPX_daily, SPX_fwd_5d, SPX_fwd_21d]
```

### 6.3 The hypothesis list

Order matters — this ordering is itself the intellectual content of the study. Display it in this order, not sorted by p-value.

**Tier 1 — has a plausible causal mechanism.** Treat these seriously; they are not jokes.

| id | notes |
|---|---|
| geomagnetic_storms | Kp index ≥ 7 from NOAA, free, back to 1932. Krivelyova & Robotti, Atlanta Fed WP 2003-5: storms affect mood, mood affects risk appetite, people misattribute a bad mood to bad economic prospects. Replicate their specification first. |
| seasonal_affective | Kamstra, Kramer & Levi — length of night by latitude |
| daylight_saving | clock-change weekends |

**Tier 2 — cyclical, no mechanism.**

| id | notes |
|---|---|
| lunar_phase | 8 phase buckets. Yuan & Zheng have a real paper to replicate against. |
| lunar_perigee | supermoon / distance percentile |
| lunar_nodes | 18.6-year node cycle; basis of McWhirter's 1938 theory |
| eclipses_solar, eclipses_lunar | event study, ±5 day window |
| saros_cycle | 18y 11d 8h |
| sunspot_number | Schwabe ~11y; SILSO data, free |
| metonic_cycle | 19 years |

**Tier 3 — pure astrology. Saturn lives here.**

| id | notes |
|---|---|
| jupiter_saturn_conjunction | "great conjunction," ~20y — the most-cited planetary market cycle |
| saturn_return | ~29.5y orbital period |
| saturn_uranus_aspect | mundane astrology's crisis aspect |
| saturn_pluto_aspect | ditto |
| mercury_retrograde | ~3×/year. Highest event count → best-powered test in the whole study. Say so; it is the fairest possible shot. |
| venus_retrograde, mars_retrograde | |
| planetary_aspects_generic | conjunction 0°, sextile 60°, square 90°, trine 120°, opposition 180°, ±3° orb, all pairs |
| bradley_siderograph | The best single addition. Donald Bradley, 1948, *Stock Market Prediction: The Planetary Barometer and its Recent Growth*. An actual weighted-sum formula over planetary aspect angles producing a daily curve. Fully computable, no interpretation required, and Bradley claimed it marks turning points rather than direction — that specificity makes it genuinely falsifiable, which most of this isn't. Source the exact weights from the published work and cite them in config; do not invent them. If unobtainable, document your reconstruction as a reconstruction. |

**Tier 4 — calendar effects, as a control group.** These are the honest contrast: some have survived scrutiny better than the celestial set, and including them shows you can discriminate.

`january_effect, sell_in_may, turn_of_month, day_of_week, santa_claus_rally, triple_witching, opex_week`

### 6.4 Variable module contract

Every module exposes one function:

```python
def compute(dates: list[date]) -> pl.DataFrame:
    """Returns columns: date, value, bucket. Deterministic. No lookahead."""
```

Ephemeris values come from Skyfield with the DE440s kernel. No modulo-29.5 approximations anywhere — use real astronomy. The moon page renders from the same source, so an approximation would be visible.

### 6.5 Statistical harness

Identical treatment for every hypothesis. No per-hypothesis tuning — that is the whole design.

- **Return series:** SPX daily log returns, plus forward 5-day and 21-day, from 1960 where available.
- **Point estimate:** mean return by bucket, difference versus the complement.
- **Standard errors:** Newey–West HAC, lag floor(4·(N/100)^(2/9)).
- **Confidence intervals:** stationary bootstrap (Politis & Romano 1994), 10,000 resamples, expected block length ~21 days to preserve autocorrelation.

**Multiple comparisons across the entire family:**

- Benjamini–Hochberg FDR at q = 0.10
- Bonferroni at α = 0.05
- Report both. They disagree, and the disagreement is informative.

**Power analysis, per hypothesis:** minimum detectable effect at 80% power given N. Report it next to the result. For most of these the honest finding is "you would need more data than exists," and that is the result worth publishing.

**Output per hypothesis:** effect size, 95% CI, raw p, BH-adjusted p, Bonferroni-adjusted p, N observations, N events, minimum detectable effect.

### 6.6 The leaderboard — where the butter reference lives

One table. Every hypothesis ranked by raw p-value. A horizontal rule drawn at the corrected significance threshold.

Label that rule the **Bangladesh line.** Directly beneath it, one footnote:

> In 1994, David Leinweber and Dave Krider searched a UN database of non-financial country statistics and found that butter production in Bangladesh explained 75% of S&P 500 variation from 1981–1993. Adding US cheese production took it to 95%. Adding the sheep populations of Bangladesh and the US took it to 99%. It was published as *Stupid Data Miner Tricks.* Everything above this line is butter.

That is the entire reference. Do not build a butter page. It is funnier compressed, it is load-bearing rather than decorative, and it teaches multiple-comparisons correction to a general reader in three sentences.

---

## 7. Site

### 7.1 Pages

| Route | Contents |
|---|---|
| `/` | Today's SPX surface, full-bleed and interactive. Archive counter line. One paragraph stating the thesis. Nothing else. |
| `/vol/[ticker]` | Per-ticker: 3D surface, skew curve, term structure, IV rank, RV estimator comparison, GEX by strike, history sparklines with percentile ranks |
| `/archive` | Date scrubber replaying any past session. Deep-linkable: `/archive/2026-03-14` |
| `/methodology` | The long-form technical writeup. Forward extraction, SVI, arbitrage conditions, greek conventions and units, the GEX sign assumption, filtering rules, known limitations. This page gets you hired — write it like a paper, with equations rendered via KaTeX. |
| `/quality` | The daily QC report, plus history. Crossed markets, zero-bid share, fit failures, exclusion rates. Nobody publishes this and it is oddly compelling. |
| `/study` | Pre-registered celestial study. The leaderboard with the Bangladesh line. Link to the pre-registration commit. |
| `/study/[hypothesis]` | Per-hypothesis detail: variable time series, bucketed returns, effect size with CI, power analysis, honest conclusion |
| `/study/moon` | The showpiece. See §7.2. |
| `/research` | Replication of the variance risk premium, then one original test, honestly reported |
| `/data` | Direct Parquet downloads + the DuckDB-WASM SQL console |
| `/about` | What this is, who built it, what it is not |

Put `/study` and its children under a "Curiosities" nav group, clearly secondary. Never on the homepage. If the site reads as a joke site first, the serious work stops counting.

### 7.2 The moon page

Make the astronomy genuinely correct and the joke gets much better.

**Render:** three.js sphere with NASA LRO albedo and displacement maps. Correct terminator from the real sun-earth-moon geometry. Correct libration. Correct phase for the current moment.

**Data:** phase angle, illumination fraction, exact timestamps of the next new and full moon, lunar distance with perigee/apogee marked.

Directly beneath the pretty render: the live statistical test. SPX returns bucketed by phase, N, effect size, CI, p-value, recomputed daily. Plus the power analysis — how many years you would need to detect a 5bp/day effect versus how many exist.

The joke only lands because the rigor underneath it is real. A sloppy moon page is a gimmick.

### 7.3 The homepage line

```
Updated 4:32 PM ET today  ·  187 trading days archived  ·  2,431,908 contract-days
```

Someone spends ninety seconds on your resume link. This line tells them the system is real, current, and has been running a long time before they read a word of methodology. Generate the numbers at build time from the actual archive — never hardcode them.

### 7.4 Design direction

Ground the design in the subject: an astronomical observatory that publishes a data archive. The vernacular of nautical almanacs and observatory plate catalogues — dense numeric tables, precise, undecorated, but with genuinely beautiful plates.

**Palette.** Observatories light their floors in amber and red so astronomers keep dark adaptation. That is the literal grounding for amber-on-dark here, and it is why the accent is not arbitrary.

```
--void:        #0B1017   /* page ground, blue-shifted night, not tinted black */
--panel:       #141C26   /* data panels, one step lifted */
--rule:        #26313D   /* hairlines */
--type:        #E6E3DB   /* warm off-white body */
--type-muted:  #8A96A3
--amber:       #F0A860   /* interactive states, sampled from the surface colormap */
--fault:       #C4553D   /* arbitrage violations, QC failures */
```

The vol surface uses a perceptually uniform sequential ramp (magma). The UI accent is sampled from that ramp, so the accent color is the data colormap rather than a brand decision.

**Type.** Literata for body — a serif with sturdy weights that hold up on dark grounds and genuinely good tabular figures. Iosevka for numeric columns — condensed, built for data density, and much less seen than the usual monospace choices.

Use monospace only for actual aligned numeric columns, where alignment is functional. Do not use it for small labels or metadata strings; that is a stylistic tic, not a choice.

**Hero.** The most characteristic thing in this world is a volatility surface. So the homepage hero is a live rendered SPX surface, full-bleed and rotatable. Not a big number with a gradient. Not a stat grid.

Hairline rules on data tables are functional here — this is an almanac and the tables are dense. That justification does not extend to the rest of the site. Do not turn everything into a broadsheet.

**Avoid:** cream backgrounds with terracotta accents; all-caps tracked-out eyebrow labels above headings; identical rounded cards with soft grey shadows; arrows appended to link text; metadata strings joined with middle dots; fade-and-slide-up entrances on every section.

**Motion.** One orchestrated moment only: the surface settling into place on page load. Nothing else animates unless a person acts on it. Respect `prefers-reduced-motion`.

### 7.5 Copy voice

Plain, declarative, unhurried. Report findings without selling them. Never use "discover," "unlock," "powerful," or "insights." When a result is null, say it is null in the first sentence rather than burying it. The confidence of the writing should come from precision, not adjectives.

---

## 8. Build & Deploy

`.github/workflows/daily.yml`:

- Check the market calendar; exit silently if not a completed trading day
- Ingest chains and underlying bars → write Parquet to R2
- Ingest ephemeris and Kp index
- Compute forwards, surfaces, greeks, metrics, aggregates
- Run quality checks → write the daily report
- Re-run the study weekly (Sunday), not daily — it does not change fast enough to justify it
- Export site JSON
- Trigger the Cloudflare Pages build
- On failure: open a GitHub issue automatically. Do not fail silently. A gap in the archive is worse than a loud error.

Everything the site serves is a static file. Nothing renders on request.

---

## 9. Build Order

Do not build these in parallel. The archive starts accruing at milestone 1, so ship that first even if everything downstream is crude.

| # | Milestone | Done when |
|---|---|---|
| 1 | Snapshot job on cron | Chains are landing in R2 daily, unattended. Ship this in week one. Everything else is an improvement made while the clock is already running. |
| 2 | One-page site | Domain resolves, states what this is, shows one SPX surface |
| 3 | Forward extraction + SVI + arbitrage checks | Surfaces fit, violations detected and logged |
| 4 | Full metrics table | §5.4–5.8 computing daily |
| 5 | Per-ticker pages + archive scrubber | Deep-linkable history browsing |
| 6 | Methodology page | Written properly, with equations |
| 7 | Quality page | QC history published |
| 8 | Pre-registration commit | PREREGISTRATION.md frozen and timestamped |
| 9 | Hypothesis engine + harness | All ~30 hypotheses running |
| 10 | Study pages + leaderboard + moon page | Bangladesh line rendered |
| 11 | DuckDB-WASM console | Browser SQL over the archive |
| 12 | Research: VRP replication, then one original test | Replication validates the pipeline before any novel claim |

Milestone 12's order is deliberate. Reproduce a known result first. If you cannot recover a documented effect, something upstream is broken, and you want to find that out before you go looking for anything new.

---

## 10. Acceptance Criteria

- [ ] Pipeline has run unattended for 30 consecutive trading days with no manual intervention
- [ ] Every published surface satisfies Durrleman and calendar conditions, or is explicitly labeled as a violation with min_g shown
- [ ] Forward extraction R² ≥ 0.99 on every accepted expiry
- [ ] No raw real-time quotes published anywhere
- [ ] Archive is reconstructable to any past date with no lookahead
- [ ] Pre-registration commit predates the first study result commit — verifiable in git history
- [ ] Multiple-comparisons correction applied across the full family, both methods reported
- [ ] Every metric on every page traces to a documented formula on `/methodology`
- [ ] `git clone && make daily` reproduces a day's output from raw inputs
- [ ] Site scores ≥ 95 on Lighthouse accessibility; keyboard focus visible throughout
- [ ] Zero backtest equity curves in the repository

---

## 11. Final Note to the Builder

Two failure modes matter more than any technical detail.

**Do not let the fun outgrow the instrument.** The celestial study is a demonstration attached to a real archive. If a visitor's first impression is astrology, the volatility work stops reading as serious, and the volatility work is the part that gets interviews.

**Do not include anything that cannot be defended in depth.** A quant interviewer will drill into this project as a proxy for drilling into the person who built it. Every equation on the site should be one its author can derive on a whiteboard. If something is copied in without being understood, take it out.
