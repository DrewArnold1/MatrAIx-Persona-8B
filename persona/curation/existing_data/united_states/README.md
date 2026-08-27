# US adult calibration targets

Marginal targets and a raking front-end for building a **US-adult** persona
cohort, in the shape of the Philippines target set next door.

- `targets_us.json` — population shares for US adults 18+, per schema dimension
- `rake_us_adults.py` — filter, rake or draw, and report what the cohort can and
  cannot support
- tests: `tests/persona/curation/existing_data/test_united_states_targets.py`

The calibration itself is the existing, tested
`persona/post_process/coreset_1m/calibration.py` — this directory supplies the
targets and the US-specific filtering, nothing more.

## Read this before using the output

**These shares are hand-authored and unverified.** The authoring environment had
no network route to census.gov, api.census.gov or bls.gov, so no figure was
fetched or reconciled against a source file. They are approximations from
recalled published tables, good to roughly ±1–2 points on the well-measured
dimensions and materially worse where `quality` is `low`. `targets_us.json`
carries `"status": "draft_unverified"`, and a test asserts that flag so
regenerating from primary sources has to be a deliberate act.

Fit for pilot work, method development and plumbing tests. Not fit for a
published estimate.

**The schema has no country dimension.** The finest geography is
`region = "North America"`, which pools the United States with Canada and
Mexico. `--us-culture` narrows further using `cult_united_states`, but that
measures cultural familiarity rather than residence and is sparsely populated.
Whatever comes out is *North American adults reweighted to US marginals*. The
output JSON says so in its `interpretation` field. Do not report it as a US
population estimate.

## Why the 1M coreset is not already US-representative

`persona/post_process/coreset_1m/targets.json` is calibrated to
`global_population_2024` against UN WPP, with North America at 4.8% of the
world. Persona 1M is a *global* sample. Filtering it to North America gives you
North Americans distributed to global-fit margins, not US ones — which is
exactly what this target set is for.

## Two modes

```bash
python persona/curation/existing_data/united_states/rake_us_adults.py \
  --pool persona/datasets/matraix-persona-dev-sample \
  --mode weight --out jobs/us_adults_cohort.json
```

**`--mode weight`** keeps every eligible persona and assigns a calibration
weight. Correct when the pool is small: with ~50 eligible personas you cannot
afford to discard any, so you run them all and weight the answers.

**`--mode sample`** draws a fixed-size cohort whose own margins match the
targets, via `calibrate_inclusion_weights` + `deterministic_priority_sample`
(order-independent and seed-stable). Correct when the pool is large — Persona
1M — and the binding constraint is how many trials you can pay for.

Default calibration dimensions come from `default_rake_dimensions`:
`age_bracket`, `gender_identity`, `highest_education`, `demo_ethnicity_broad`,
`urbanicity`. Override with `--dims`. `demo_employment_status`,
`demo_marital_status` and `political_lean` are present but advisory — opt in
deliberately.

## Two traps this script exists to close

**Minors in the pool.** `rake_weights` counts every row with a non-negative code
in the margin denominator. A 12-year-old carries a perfectly valid age code, so
leaving minors in inflates the denominator and pushes every adult share down
while the minors keep unadjusted weight. `targets_us.json` is an adults-only
target set and the script enforces the filter.

**Partial margins.** Omitting a category that the pool actually contains does
not degrade gracefully — that category's rows sit in the denominator, never
targeted, deflating every share you did supply. The script checks omissions
*empirically against the filtered pool*: an omitted category with zero rows is
fine (this is what makes the adults-only age target correct), and an omitted
category with rows is a hard error.

`socioeconomic_band` is deliberately empty and must stay that way for
economic-pressure work. There is no published US marginal for a five-band
subjective construct, and household finances are the dependent variable there —
raking on them would bake the finding into the sample.

## Read the diagnostics, not just the residuals

A small residual is not evidence of representativeness. Two things in the output
matter more:

**`diagnostics.effective_n`** — Kish's effective sample size, and the design
effect it implies. Raking an unrepresentative pool buys margin fit with
statistical power.

**`structural_gaps`** — the failures reweighting *cannot* fix. A target category
with zero eligible personas stays at zero however the weights move; its residual
looks like a near miss when it is actually an unreachable category. Margins fit
on a handful of coded rows are flagged separately, because a tiny residual there
is overfitting rather than representativeness.

## What the dev sample actually supports

Running `--mode weight` against `persona/datasets/matraix-persona-dev-sample`
(200 personas, README-designated smoke-only):

```
200 personas -> 56 eligible US-adult proxy
n=56  effective_n=12.56  design_effect=4.457
WARNING: weighting discards 78% of the statistical power.
COVERAGE HOLE age_bracket: no personas in [75-84, 85+] = 9.5% of US adults
COVERAGE HOLE demo_ethnicity_broad: no personas in
  [South Asian, Southeast Asian, Middle Eastern, Indigenous, Pacific Islander]
  = 5.2% of US adults
THIN MARGIN urbanicity: only 8 personas carry this dimension
```

Read plainly: the dev sample cannot support US-representative claims. Raking
gets the margins it *can* reach to within a few points, but 56 personas become
an effective 12.6, roughly 15% of US adults live in categories the pool contains
nobody from, and the urbanicity fit rests on 8 records. Those are coverage
holes, not tuning problems — no weighting scheme fixes them.

Use the dev pool to develop the method and exercise the pipeline. For cohorts
meant to support a finding, import the 1M coreset:

```bash
python persona/scripts/fetch_persona_1m.py
```

That downloads the release, verifies the on-disk layout against what the pool
loader actually globs for, and prints the next command. If Hugging Face is
blocked by an egress policy it says so and names the hosts to allow instead of
retrying.

## What the 1M release actually supports

Measured against the downloaded release (999,847 rows), not projected:

```
999847 personas -> 143336 eligible US-adult proxy
n=143336  effective_n=35451.3  design_effect=4.043
WARNING: weighting discards 75% of the statistical power.
```

143,336 eligible adults, ~2,560x the dev sample's 56 — far more than the
~34,000 an earlier draft of this section projected from the 4.8% region share.
Every coverage hole the dev sample had is gone: `structural_gaps` comes back
empty, every target category has eligible personas, and raking lands the
margins to within 0.0018 on age, gender, education and urbanicity.

That fixes coverage. It does not fix the other two problems.

**Sparsity.** `coded_rows` per margin, as a share of the 143,336 eligible:

| margin | coded | share | smallest cell |
|---|---|---|---|
| `highest_education` | 128,754 | 89.8% | Postdoc = 390 |
| `gender_identity` | 113,972 | 79.5% | Prefer not to say = 1 |
| `age_bracket` | 109,610 | 76.5% | 85+ = 2,660 |
| `demo_ethnicity_broad` | 101,989 | 71.2% | Southeast Asian = 5 |
| `urbanicity` | 42,821 | 29.9% | Nomadic / remote = 73 |

`urbanicity` is still carried by fewer than a third of eligible records. Pool
size makes a rarely populated dimension bigger in absolute terms, not denser,
and `rake_weights` only calibrates rows that carry a value.

**Cells too thin to reach.** `structural_gaps` flags a category with *zero*
rows. It does not flag one with five. `demo_ethnicity_broad` / Southeast Asian
has 5 eligible records against a 2.0% target, and raking reaches 0.16% — a
12x shortfall, and the largest residual in the whole fit. Pacific Islander is
the same shape (n=5). Read the per-category `n` alongside the residual; a
category that is present but negligible behaves like a hole and is not
reported as one.

## Choosing a cohort size

The 38 target cells across the five default dimensions set a floor. A draw of
40 personas has roughly one persona per cell and cannot match the targets it
was drawn against — its own margins come back 0.04 to 0.20 off, and its
`urbanicity` fit rests on 13 records:

```bash
python persona/curation/existing_data/united_states/rake_us_adults.py \
  --pool persona/datasets/matraix-persona-1m --mode sample --sample-size 400
```

Note also that `--mode sample` reports `effective_n` equal to `n` and a design
effect of exactly 1.0. That is not a quality signal: a drawn cohort carries
unit weights by construction, so Kish's formula has nothing to measure. The
diagnostic that matters for a draw is how far its realized margins sit from
the targets, and how many rows carry each dimension.

## Regenerating the targets properly

Rebuild from ACS 1-year PUMS (age, sex, educational attainment, race and
Hispanic origin) and CPS ASEC (employment, marital status), following
`persona/curation/existing_data/scripts/derive_targets_ph.py`: deriving a target
from the same crosswalk that produces the data makes the partial-margin bug
impossible.

Two dimensions cannot be fixed by a table lookup and need an explicit crosswalk
decision:

- **`urbanicity`** — the Census Bureau publishes an 80/20 urban/rural split but
  does not define "suburban". The Dense urban / Suburban / Small town
  decomposition is a judgment call, and self-identification surveys give
  materially different numbers.
- **`demo_ethnicity_broad`** — the schema's categories are not OMB race and
  ethnicity. The single ACS "Asian" group has to be split into East / South /
  Southeast, and Middle Eastern has to be carved out of census "White".

Both are documented per-dimension in `targets_us.json` under `coverage`.
