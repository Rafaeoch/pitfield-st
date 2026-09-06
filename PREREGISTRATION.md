# Pre-registration — celestial and calendar hypotheses

**Status:** frozen at the commit that introduced this file.
**Written before any test code existed.** That ordering is the point.

### An honest note about the git history

This document was genuinely written before `pipeline/study/harness.py` existed —
that is the actual order the work happened in. But the repository was not
initialised until after the study had already been run, so the commit history
is a *reconstruction* of that order, not an independent record of it. The
commits below are minutes apart, not weeks.

What that means in practice: the ordering in this repository is evidence of
intent and of how the project is meant to work. It is **not** cryptographic
proof that the hypothesis list predates the results, because a reconstructed
history cannot provide that. Anyone evaluating this study should read the claim
at that strength and no higher.

From this commit forward the guarantee is real: every future amendment,
hypothesis, and result is timestamped by a repository that already exists, and
a public remote makes those timestamps third-party evidence. The weakness is
confined to the first few commits, and saying so is cheaper than being caught
implying otherwise.

If this document is edited after study results are published, the edit does not
amend the pre-registration. Adding a hypothesis later requires a new
pre-registration commit, a new family, and separate reporting with its own
multiple-comparisons correction. Appending quietly to the list below would
invalidate the whole exercise.

---

## 1. Question

Do any of the celestial or calendar variables listed in §4 have a detectable
association with equity index returns, after correcting for the fact that we are
testing roughly thirty of them at once?

The expected answer is no, for nearly all of them. The purpose of the study is
not to find an effect. It is to demonstrate, on a pre-registered basis and with
the same statistical machinery used elsewhere in this project, how readily a
family of tests this size produces apparently significant results by chance.

## 2. Data

**Return series.** Daily log returns of a broad US equity index. See
deviation D1 in §9: the primary series is the **NASDAQ Composite from February
1971** (~14,000 observations, FRED series `NASDAQCOM`), with the **S&P 500**
retained as a secondary robustness check over the shorter window FRED is
licensed to publish (`SP500`, ~2,500 observations).

Three horizons are tested for every hypothesis:

| Series | Definition |
|---|---|
| `IDX_daily` | log return, close to close, on the day the variable is observed |
| `IDX_fwd_5d` | log return over the following 5 trading days |
| `IDX_fwd_21d` | log return over the following 21 trading days |

Forward windows are truncated at the end of the sample rather than padded. An
observation whose forward window would run past the last available date produces
no return, because inventing one is a lookahead bug.

**Celestial variables.** Computed with Skyfield against the DE440s ephemeris
kernel. No modulo approximations anywhere — the astronomy is real, and the same
computation drives the rendered moon page, so an approximation would be visible.

**Geomagnetic data.** Planetary K-index from **GFZ Potsdam**, the issuing
authority, which serves the full 3-hourly record from 1932. NOAA SWPC publishes
only a recent window and is used for the live tail, not for the study. Kp is
aggregated to a daily maximum, since Krivelyova & Robotti define a storm on the
daily series and a daily mean would dilute a six-hour severe disturbance.

**Solar data.** Daily total sunspot number from SILSO, Royal Observatory of
Belgium.

**Trading calendar.** `pandas-market-calendars`, so holidays, half days and
daylight-saving transitions are handled rather than assumed.

## 3. Bucketing

Each variable produces, for each date, a value and a bucket. Bucketing rules are
fixed here and may not be adjusted after seeing results — re-bucketing until
something appears is the most common way a study like this quietly becomes a
fishing expedition.

- **Categorical variables** (lunar phase, day of week) use their natural buckets.
- **Continuous variables** (Bradley siderograph, sunspot number, night length)
  are split at terciles of their own full-sample distribution.
- **Event variables** (eclipses, retrogrades, storms) are tested as an event
  study with a ±5 trading-day window, against all non-event days as the
  complement.

## 4. Hypothesis list

Frozen. The registry is `pipeline/study/hypotheses.yaml`, and it must match this
list exactly. Ordering is by prior plausibility and is retained in all reporting;
results are never sorted by p-value.

### Tier 1 — plausible causal mechanism

| id | source |
|---|---|
| `geomagnetic_storms` | Krivelyova & Robotti, Atlanta Fed WP 2003-5. Kp ≥ 7. Their specification is replicated first, before any variant. |
| `seasonal_affective` | Kamstra, Kramer & Levi. Length of night by latitude. |
| `daylight_saving` | Clock-change weekends. |

### Tier 2 — cyclical, no mechanism

`lunar_phase` (8 buckets; Yuan & Zheng), `lunar_perigee`, `lunar_nodes`,
`eclipses_solar`, `eclipses_lunar`, `saros_cycle`, `sunspot_number`,
`metonic_cycle`

### Tier 3 — astrology

`jupiter_saturn_conjunction`, `saturn_return`, `saturn_uranus_aspect`,
`saturn_pluto_aspect`, `mercury_retrograde`, `venus_retrograde`,
`mars_retrograde`, `planetary_aspects_generic` (conjunction 0°, sextile 60°,
square 90°, trine 120°, opposition 180°; ±3° orb; all pairs),
`bradley_siderograph`

`mercury_retrograde` has the highest event count in the study and is therefore
the best-powered test in it. This will be stated in the results: it gets the
fairest possible shot.

`bradley_siderograph` uses Bradley's published weights (1948). If the exact
weights cannot be sourced, the reconstruction is documented as a reconstruction
and labelled as such in the results.

### Tier 4 — calendar effects, as a control group

`january_effect`, `sell_in_may`, `turn_of_month`, `day_of_week`,
`santa_claus_rally`, `triple_witching`, `opex_week`

These are included as an honest contrast. Some have survived scrutiny better
than anything in Tiers 2 and 3, and a harness that cannot distinguish them from
the celestial set is not measuring anything.

## 5. Test statistic

For each (hypothesis × return series):

- **Point estimate:** mean return by bucket, and the difference between each
  bucket and its complement.
- **Standard errors:** Newey–West HAC, lag `floor(4 (N/100)^(2/9))`.
- **Confidence intervals:** stationary bootstrap (Politis & Romano 1994), 10,000
  resamples, expected block length 21 trading days, to preserve autocorrelation.

## 6. Multiple comparisons

Applied across the **entire family** — every hypothesis, every bucket contrast,
every return series — not within tiers and not within hypotheses.

- Benjamini–Hochberg FDR at q = 0.10
- Bonferroni at α = 0.05

Both are reported. They will disagree, and the disagreement is itself
informative.

## 7. Power

For every hypothesis, the minimum effect detectable at 80% power given the
number of events that actually exist, reported next to the result. For most of
this list the honest finding will be that detecting a plausible effect would
require more data than the market has produced, and that finding is published
with the same prominence as any rejection.

## 8. What gets reported

Everything. Every hypothesis in the list appears in the results with effect
size, 95% confidence interval, raw p, BH-adjusted p, Bonferroni-adjusted p, N
observations, N events, and minimum detectable effect — including, and
especially, the null results.

No hypothesis is dropped for being uninteresting. No hypothesis is added for
being interesting.

## 9. Deviations

Any departure from this document is recorded here, dated, with the reason, and
surfaced on the results page. An unrecorded deviation is a broken
pre-registration.

### D1 — Return series changed from S&P 500 (1960) to NASDAQ Composite (1971)

**Date:** 2026-09-02. **Recorded before any study code was written and before
any result was computed**, which is the only circumstance in which a
pre-registration may be amended rather than superseded.

**Reason.** The original series was unobtainable under the project's own
constraint that the study be reproducible by anyone who clones the repository,
with no paid key and no scraping. Every free daily S&P 500 source checked was
either licence-capped, blocked, or discontinued:

| Source | Outcome |
|---|---|
| FRED `SP500` | works, but S&P licensing limits FRED to a trailing 10-year window (~2,500 obs) |
| Stooq | now serves a JavaScript proof-of-work challenge; not programmatically accessible |
| MeasuringWorth | dataset endpoint returns 404 |
| FRED Wilshire 5000 | all series IDs discontinued |
| Yahoo Finance | terms of service do not permit this use |
| Tiingo / Alpha Vantage / Polygon | require an API key, breaking free reproducibility |

FRED's `NASDAQCOM` carries no such restriction and runs from 1971-02-05, giving
roughly 14,000 daily observations — enough for the 11-year solar cycle and the
18.6-year lunar node cycle to complete several times, which 10 years of S&P data
emphatically is not.

**Consequences, stated rather than buried.**

1. The NASDAQ Composite is more concentrated in technology and has higher
   volatility (~20% annualised over the sample) than a broad-market index. Any
   effect found is an effect in that index, not in "the market".
2. The sample begins in 1971, not 1960, so N is smaller than originally planned
   and every power analysis is correspondingly weaker. The minimum detectable
   effects reported in the results already reflect the actual N.
3. The S&P 500 remains in the study as a secondary series. Where the two
   disagree, both are reported.

This deviation was forced by data availability, not chosen after inspecting
results — no result existed when it was made.
