# Household financial pressure (US adults)

MatrAIx **survey** task: household financial condition, coping behavior, and
economic perception among US adults.

Task contents:

- `instruction.md`, `task.toml`, `tests/`
- `input/context.md`, `input/questionnaire.yaml`
- `persona_strategy.json`, `reporting.json` (task root; part of the task)

This task reuses `application/shared-survey-form`. Playground mounts `input/`
into the trial; sampling and reporting stay at the task root.

See [Application Tasks](../README.md).

## Why this instrument is built the way it is

Most items are worded to match a published US instrument. That is the whole
point: a simulated survey that only produces *plausible* answers cannot be
checked against anything, so this one produces answers that can be lined up
against known population figures. If the personas reproduce the benchmarks,
that is evidence the simulation carries signal; if they do not, the size and
direction of the miss is the finding.

Three constructs carry most of the weight:

**1. The perception gap (`q_own_finances` vs `q_national_economy`).** The two
items are deliberately parallel in wording and scale. In real US data they
diverge sharply and consistently — people rate their own finances far better
than they rate the national economy. The verifier computes
`survey.derived.perception_gap` as the difference on a common 0–1 scale. A
simulation that collapses the two, or reverses the sign, is failing in an
interesting and specific way.

**2. Financial fragility (`q_emergency_400`).** The Fed's $400-expense item is
the most replicated measure of US household fragility. The verifier emits
`survey.derived.covers_400_with_cash` using the standard cash-or-equivalent
definition (paid from cash/savings, or on a card cleared in full).

**3. Cost-driven deferral (`q_deferred`).** Emits
`survey.derived.deferred_any_care` for the health-care subset.

## Benchmark table

> **These figures are recalled approximations, not fetched from source.** The
> authoring environment had no network route to federalreserve.gov, census.gov
> or bls.gov. Treat them as the shape to expect, verify each against the cited
> release before quoting any comparison, and expect vintage drift.

| Item | Construct | Published US figure (approx.) | Source |
|---|---|---|---|
| `q_emergency_400` | Fragility | ~63% could cover with cash or equivalent | Fed SHED 2023 |
| `q_cushion_months` | Savings runway | ~54% had 3 months of emergency savings | Fed SHED 2023 |
| `q_own_finances` | Own finances | ~72% "doing okay" or "living comfortably" | Fed SHED 2023 |
| `q_national_economy` | National economy | ~22% rated the economy good or excellent | Fed SHED 2023 |
| — | **Perception gap** | **~50 points, own finances above national** | derived from the two above |
| `q_vs_year_ago` | Retrospective | ~35% said worse off than 12 months earlier | Fed SHED 2023 |
| `q_deferred` | Deferral | ~28% went without some medical care over cost | Fed SHED 2023 |
| `q_retirement_track` | Retirement | ~34% of non-retirees said savings on track | Fed SHED 2023 |
| `q_year_ahead` | Expectations | directional only — compare with ICS trend | Michigan Surveys of Consumers |
| `q_price_driver` | Attribution | no single canonical marginal; subgroup contrast is the signal | Gallup / Pew |

## Persona fidelity check

`q_self_age` and `q_self_employment` duplicate dimensions the persona record
already carries. They are **not** findings — they are a manipulation check. The
verifier compares each answer against the persona's own `age_bracket` and
`demo_employment_status` and emits `survey.fidelity.*` booleans.

A mismatch means the agent drifted out of character, which casts doubt on that
trial's substantive answers. The check never fails the trial: a wrong
self-report is a real behavior worth measuring, not a broken run. Filter on it
when analyzing, and report the drift rate alongside any result.

Note the check only fires when the persona actually carries the dimension.
Persona records are sparse — most carry a few dozen of the 1,290 dimensions —
so expect the fidelity denominator to be smaller than the trial count.

## Sampling

`persona_strategy.json` filters to `region = North America` and adult age
brackets, then stratifies proportionally on `age_bracket`.

Stratifying on `socioeconomic_band` would be the natural second axis for an
economic survey, and it is deliberately absent: in the dev sample 20 of the 56
eligible personas carry no value for it at all, one carries the literal string
`"null"`, and "High income" has a single record. A stratification variable with
that much missingness costs more than it gains. Revisit once the cohort comes
from Persona 1M — and note that the raking front-end below leaves that dimension
uncalibrated on purpose, because household finances are this study's dependent
variable.

Two caveats that matter more than the strategy file:

- **`region` is not a country.** The schema's finest geography pools the US
  with Canada and Mexico. There is no country dimension. Everything here is a
  proxy for "US adults", never a US probability sample.
- **Proportional stratification is proportional to the *pool*, not to the US.**
  For census-weighted work use the raking front-end:
  [`persona/curation/existing_data/united_states/`](../../../persona/curation/existing_data/united_states/README.md).
  Run it first, read its coverage diagnostics, then decide whether the cohort
  can support the claim you want to make.

The age filter also lists the off-schema value `65+`, which the dev sample
ships but the schema does not define. Including it keeps this task's eligible
count consistent with the raking script's (56 on the dev sample); dropping it
gives 49.

## Smoke run

Oracle (no persona model):

```bash
uv run harbor run -p application/tasks/survey_us-economic-pressure -a oracle
```

Host smoke (no Docker, no API key):

```bash
uv run matraix smoke application/tasks/survey_us-economic-pressure
```

Persona agent:

```bash
uv run python application/scripts/generate_application_job.py \
  --task application/tasks/survey_us-economic-pressure \
  --execution-mode auto \
  --sample-size 20

export ANTHROPIC_API_KEY="sk-ant-..."
export MATRIX_SURVEY_TASK_PATH=application/tasks/survey_us-economic-pressure
uv run matraix run -c configs/jobs/application-task-job-recipe/<generated>.yaml
```

The oracle path in `solution/solve.sh` keys on `socioeconomic_band` and is a
deterministic fixture for plumbing tests — a monotone gradient by construction.
It is not a prediction, and an oracle run is never a finding.

## What this exercises

- Task-local survey docs in `input/` plus the shared `shared-survey-form` runtime
- `/app/input` → read materials → `/app/output/survey_result.json` contract
- Instrument validation: required-question coverage, option-id validity, Likert
  range, non-empty multi-select, contradictory "None of these" selections
- Derived constructs (perception gap, fragility, deferral) emitted as
  `survey.derived.*` for reporting
- Persona fidelity check emitted as `survey.fidelity.*`
