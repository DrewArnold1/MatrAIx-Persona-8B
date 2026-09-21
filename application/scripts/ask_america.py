#!/usr/bin/env python3
"""Ask one question and get a job that answers it with a US-adult cohort.

This is the thin end-to-end path behind "how would 1000 Americans answer X":

    question -> generated survey task -> calibrated cohort -> Harbor job recipe

It composes pieces that already exist rather than adding a parallel pipeline.
``adhoc_survey_task.py`` writes the task, ``rake_us_adults.py`` draws the
cohort, and ``generate_application_job.py`` builds the job. Run the recipe this
script prints, then read the distribution with ``matraix results``.

    uv run python application/scripts/ask_america.py \\
      "Do you think the country is on the right track?" \\
      --option "Right track" --option "Wrong track" --option "Not sure" \\
      --sample-size 1000

Two things this script will not do quietly:

* It draws the cohort with ``--mode sample``, never ``--mode weight``. A
  calibrated draw is self-weighting, so the unweighted aggregation downstream
  is correct for it. Raking weights are computed by that script but are not
  carried through launch or aggregation, so ``--mode weight`` would produce
  marginals that are wrong with nothing saying so.
* It prints the standing caveats with the result, every time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from _repo_imports import REPO_ROOT, ensure_application_script_imports

DEFAULT_POOL = "persona/datasets/matraix-persona-1m"
DEV_POOL = "persona/datasets/matraix-persona-dev-sample"
RAKE_SCRIPT = (
    REPO_ROOT / "persona" / "curation" / "existing_data" / "united_states" / "rake_us_adults.py"
)

CAVEATS = """
Read the output as a simulated distribution, not a survey estimate:

  * The persona schema has no country dimension. region='North America' pools
    the US with Canada and Mexico, so the cohort is North American adults
    reweighted to US marginals.
  * targets_us.json is status=draft_unverified: hand-authored from recalled
    published tables, not derived from ACS or CPS microdata.
  * This question's wording is not matched to a published instrument, so its
    marginals cannot be checked against a real benchmark.
""".strip()


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    printable = " ".join(command)
    print("+ {}".format(printable), file=sys.stderr, flush=True)
    return subprocess.run(
        command,
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=capture,
    )


def _draw_cohort(
    *,
    pool: str,
    sample_size: int,
    out_path: Path,
    seed: int,
    eligible_cache: Path | None,
) -> list[str]:
    """Draw a calibrated US-adult cohort and return its persona ids."""
    command = [
        sys.executable,
        str(RAKE_SCRIPT),
        "--pool",
        pool,
        "--mode",
        "sample",
        "--sample-size",
        str(sample_size),
        "--seed",
        str(seed),
        "--out",
        str(out_path),
    ]
    if eligible_cache is not None:
        command.extend(["--eligible-cache", str(eligible_cache)])
    _run(command)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    personas = payload.get("personas")
    if not isinstance(personas, list) or not personas:
        raise SystemExit("cohort file {} holds no personas".format(out_path))

    gaps = payload.get("structural_gaps") or {}
    empty = gaps.get("empty_target_categories") or {}
    unreachable = gaps.get("unreachable_population_share") or {}
    if empty:
        # A coverage hole is a category reweighting cannot reach, not a tuning
        # problem. Say so here rather than letting it pass as a clean run.
        print(
            "\nWARNING: coverage holes. The pool has no personas in these target\n"
            "categories, so those people cannot appear in the answer at any weight:",
            file=sys.stderr,
        )
        for dimension, holes in sorted(empty.items()):
            values = ", ".join(str(hole.get("value")) for hole in holes)
            share = unreachable.get(dimension)
            suffix = " = {:.1%} of US adults".format(share) if isinstance(share, float) else ""
            print("  {}: [{}]{}".format(dimension, values, suffix), file=sys.stderr)
        print("", file=sys.stderr)

    return [str(entry["persona_id"]) for entry in personas if entry.get("persona_id")]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Turn one question into a runnable US-adult survey job.",
    )
    parser.add_argument("question", help="The question to ask.")
    parser.add_argument(
        "--option",
        dest="options",
        action="append",
        default=None,
        metavar="TEXT",
        help=(
            "One answer option; repeat for each. Omit entirely for a free-text "
            "question, which is the honest default when the question did not "
            "come with options of its own."
        ),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=1000,
        help="Cohort size (default: 1000).",
    )
    parser.add_argument(
        "--pool",
        default=DEFAULT_POOL,
        help=(
            "Persona pool (default: {}). The dev sample cannot support a "
            "US-representative cohort - 56 eligible adults, effective n 12.6.".format(
                DEFAULT_POOL
            )
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model-name", default="anthropic/claude-sonnet-4-6")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Parallel trials (default: scaled to cohort size by the job generator).",
    )
    parser.add_argument(
        "--context-note",
        default="",
        help="Extra respondent context prepended to input/context.md.",
    )
    parser.add_argument(
        "--segment",
        dest="segments",
        action="append",
        default=None,
        metavar="DIMENSION",
        help="Persona dimension to crosstab the answer by; repeat for each.",
    )
    parser.add_argument(
        "--ask-rationale",
        action="store_true",
        help="Also ask each persona for a short reason.",
    )
    parser.add_argument(
        "--eligible-cache",
        type=Path,
        default=None,
        help="Cache the filtered eligible set here; reused across runs.",
    )
    parser.add_argument(
        "--cohort-out",
        type=Path,
        default=None,
        help="Where to write the cohort JSON (default: jobs/adhoc-cohort-<n>.json).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate the task folder even if this question was asked before.",
    )
    parser.add_argument(
        "--task-only",
        action="store_true",
        help="Write the task folder and stop; do not draw a cohort or build a job.",
    )
    args = parser.parse_args()

    ensure_application_script_imports()

    from backend.service.adhoc_survey_task import (
        DEFAULT_SEGMENT_DIMENSIONS,
        AdhocSurveyTaskError,
        materialize_adhoc_survey_task,
    )

    if args.sample_size < 1:
        parser.error("--sample-size must be at least 1")

    try:
        task = materialize_adhoc_survey_task(
            repo_root=REPO_ROOT,
            question=args.question,
            options=args.options,
            context_note=args.context_note,
            sample_size=args.sample_size,
            segment_dimensions=args.segments or DEFAULT_SEGMENT_DIMENSIONS,
            ask_rationale=args.ask_rationale,
            overwrite=args.overwrite,
        )
    except AdhocSurveyTaskError as exc:
        parser.error(str(exc))

    question_type = task.questionnaire["questions"][0]["type"]
    print(
        "\n{} task {}".format("Reused" if task.reused else "Wrote", task.task_path),
        file=sys.stderr,
    )
    print("  questionnaire: {} ({})".format(task.questionnaire_id, question_type), file=sys.stderr)
    if question_type == "free_text":
        print(
            "  no options given, so answers are free text and aggregate as themes\n"
            "  rather than as percentages. Pass --option to get a distribution.",
            file=sys.stderr,
        )

    if args.task_only:
        print("\n{}\n".format(CAVEATS), file=sys.stderr)
        return 0

    if args.pool == DEV_POOL:
        print(
            "\nWARNING: the dev sample is smoke-only. It yields 56 eligible US "
            "adults with an effective n of 12.6 and several empty target "
            "categories, which cannot support a population claim.\n",
            file=sys.stderr,
        )

    pool_dir = REPO_ROOT / args.pool
    if not pool_dir.exists():
        raise SystemExit(
            "persona pool not found: {}\n"
            "Import the 1M coreset first:\n"
            "  python persona/scripts/fetch_persona_1m.py".format(args.pool)
        )

    cohort_out = args.cohort_out or (
        REPO_ROOT / "jobs" / "adhoc-cohort-{}.json".format(args.sample_size)
    )
    cohort_out.parent.mkdir(parents=True, exist_ok=True)
    persona_ids = _draw_cohort(
        pool=args.pool,
        sample_size=args.sample_size,
        out_path=cohort_out,
        seed=args.seed,
        eligible_cache=args.eligible_cache,
    )
    print("  cohort: {} personas from {}".format(len(persona_ids), args.pool), file=sys.stderr)

    generate = [
        sys.executable,
        str(REPO_ROOT / "application" / "scripts" / "generate_application_job.py"),
        "--task",
        task.task_path,
        "--execution-mode",
        "auto",
        "--dataset",
        args.pool,
        "--model-name",
        args.model_name,
        "--persona-ids",
        *persona_ids,
    ]
    if args.concurrency is not None:
        generate.extend(["--concurrency", str(args.concurrency)])
    _run(generate)

    print("\n{}\n".format(CAVEATS), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
