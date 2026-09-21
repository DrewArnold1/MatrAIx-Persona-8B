# Large-scale runs

Run a Playground evaluation over many personas. The result is still **one job**:
one name in Runs, one folder `jobs/<job_name>/`.

First single-persona walkthrough: [quickstart](../quickstart.md).

---

## Launch

Use one of:

- Playground
- `POST /api/harbor/jobs`
- `matraix run -c <job.yaml>`

One launch covers the whole cohort. Do not start a separate job per persona.

---

## Personas

A **cohort** for launch is a directory of persona YAML files (or a pool path
plus `useEntirePool`).

| Batch size | How to pass them |
|------------|------------------|
| Small (about ≤100) | Optional `personaIds` |
| Large | `personaPool` = that directory, plus `useEntirePool` |

Sources:

1. **Task strategy** — most tasks ship `persona_strategy.json`. Sample in
   Playground, or run
   `generate_application_job.py --task application/tasks/<name>`.
   For a stratified sample, set ``sampling.allocation`` to ``perCell`` /
   ``proportional`` / ``equalTotal``. Per-cell uses ``sampling.perCell``;
   the other two use ``sampling.sampleSize`` as the total.
2. **Public Persona 1M** —
   [`MatrAIx2026/MatrAIx_Persona_1M_Public_Release`](https://huggingface.co/datasets/MatrAIx2026/MatrAIx_Persona_1M_Public_Release).
   Import it locally, then **Pull cohort** in Playground (writes a launch cache
   under `matraix-persona-1m/cohorts/`). See
   [Persona setup](../persona/README.md#setup-and-usage).
3. **Dev sample** — `persona/datasets/matraix-persona-dev-sample/` for small
   local batches.
4. **Saved dataset** — after a pull, **Save as dataset…** copies the YAML into
   `persona/datasets/<name>/` for reuse. Playground defaults sampling to **All**.

Path taxonomy (picker vs on-disk caches):  
[Playground pools & cohorts](../persona/README.md#playground-pools--cohorts).

Playground / API fields: `personaPool`, `useEntirePool`, `sampleSize`,
`nConcurrentTrials`. Reference: [playground-api.md](../application/playground-api.md).

---

## Concurrency

`generate_application_job.py` scales `n_concurrent_trials` with the cohort:
roughly a quarter of the cohort size, capped at 16 (4 for `force_docker`, where
each trial holds a container). Override with `--concurrency N`.

Raise it only as far as the model provider's rate limit allows. Past that point
extra parallelism turns into retries rather than throughput, and a 429 storm
mid-run is harder to recover from than a slower run. A 1,000-trial survey at one
trial at a time is hours to days of wall clock, which is the failure this
default exists to prevent.

Record the persona path (and the Hugging Face revision, if you used one) so the
batch is reproducible.

---

## Outputs

```text
jobs/<job_name>/
├── result.json
├── <task>__<trial>/
│   ├── agent/
│   ├── verifier/
│   └── result.json
└── job.log
```

Keep `jobs/<job_name>/` and any sampled cohort directory if you need to
reproduce the run. Pulled launch caches sit next to the source dataset, for
example:

- `persona/datasets/matraix-persona-1m/cohorts/cohort-<digest>/`
- `persona/datasets/matraix-persona-dev-sample/cohorts/cohort-<digest>/`

Those caches (and `generated-persona-dev-*`, `saved-cohorts/`) are gitignored.

---

## Related

- [Runtime](runtime.md)
- [Quickstart](../quickstart.md)
- [Persona 1M](../persona/README.md#public-coreset-matraix-persona-1m)
- [Playground API](../application/playground-api.md)
