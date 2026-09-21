"""Materialize a runnable survey task from a question asked at runtime.

Every survey questionnaire in this repo is a folder under ``application/tasks/``
that someone authored by hand. That is the right shape for an instrument meant
to be reviewed, benchmarked and re-run — and the wrong shape for "what would
1000 Americans say about X", where the question arrives one minute and the
answer is wanted the next.

This module closes that gap without inventing a second convention: it writes a
real task folder. The generated task is discovered by the existing registry
(``survey_questionnaire_id_for_task_folder`` globs ``application/tasks/survey_*``
and reads the questionnaire id straight out of ``input/questionnaire.yaml``),
runs through the existing job generator, and aggregates through the existing
``question_response`` contexts. Nothing downstream needs to know the task was
generated rather than authored.

Generated folders are named ``survey_adhoc-<digest>`` and are gitignored. The
digest is content-derived, so asking the same question twice reuses one folder
instead of littering the tree.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

ADHOC_TASK_PREFIX = "survey_adhoc-"

# Mirrors application/tasks/survey_*/task.toml. Survey tasks all share one
# environment definition; nothing here is per-question.
_TASK_TOML_TEMPLATE = """version = "1.0"
artifacts = [ "/app/output",]

[task]
name = "application/{task_name}"

[metadata]
difficulty = "easy"
type = "survey"
domain = "{domain}"
tags = [{tags},]


[verifier]
timeout_sec = 120.0

[agent]
timeout_sec = 600.0

[environment]
definition = "application/shared-survey-form"
build_timeout_sec = 600.0
cpus = 1
memory_mb = 2048
storage_mb = 10240
gpus = 0
"""

_INSTRUCTION_TEMPLATE = """# {title}

{lead}

Read the respondent context, then answer every question in the questionnaire.
Answer as yourself.

## How to answer

- Read `input/context.md` before you start.
- Answer every required question.
- For multiple-choice, use the listed option ids.
- For "select all that apply", return a list of option ids.
- For rating scales, use a whole number in the given range.
- Answer accurately, not agreeably. There is no expected answer here, and an
  answer that makes you look inconsistent, uninformed or unflattering is a
  normal answer.
- Give the answer alone unless a question also asks for a short reason.
"""

_CONTEXT_TEMPLATE = """# Respondent context

You are answering a short survey. Nobody you know will see your answers, and
they are not attached to your name.

{extra}Answer from your own circumstances, habits and opinions as they actually
are — including when you have no opinion, have not thought about the topic
before, or would not normally answer a question like this at all.
"""

# Default crosstabs. These are the dimensions a US-population question is
# almost always read by; a caller can override them.
DEFAULT_SEGMENT_DIMENSIONS = ("age_bracket", "gender_identity", "highest_education")


@dataclass(frozen=True)
class AdhocSurveyTask:
    """A materialized ad-hoc survey task on disk."""

    task_path: str
    task_dir: Path
    questionnaire_id: str
    folder_name: str
    questionnaire: dict[str, Any]
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskPath": self.task_path,
            "questionnaireId": self.questionnaire_id,
            "folderName": self.folder_name,
            "questionnaire": self.questionnaire,
            "reused": self.reused,
        }


class AdhocSurveyTaskError(ValueError):
    """The question could not be turned into a valid questionnaire."""


def _slug(value: str, *, max_length: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_length].strip("-")


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _task_name_for(question: str, digest: str) -> str:
    """Harbor task name, which Playground turns into the picker's card title.

    Keeps the question in the name so the card reads as the question, and keeps
    a short digest so two questions that slug alike stay distinct.
    """
    slug = _slug(question, max_length=56) or "question"
    return "ask-{}-{}".format(slug, digest[:6])


def _title_from_question(question: str) -> str:
    text = " ".join(question.split())
    if len(text) <= 72:
        return text.rstrip("?.!") or "Ad-hoc question"
    return text[:69].rsplit(" ", 1)[0] + "…"


def build_questionnaire(
    *,
    question: str,
    options: Sequence[str] | None = None,
    questionnaire_id: str,
    title: str = "",
    description: str = "",
    ask_rationale: bool = False,
) -> dict[str, Any]:
    """Build a one-question questionnaire payload.

    ``options`` given → a ``single_choice`` item whose option ids are derived
    from the option text. ``options`` omitted → a ``free_text`` item, which is
    the honest default: inventing answer options for a question nobody wrote
    options for puts the framing you are trying to measure into the instrument.
    Free-text answers aggregate through ``llm_bucket_summary`` instead.
    """
    prompt = " ".join(str(question).split())
    if not prompt:
        raise AdhocSurveyTaskError("question must not be empty")

    question_id = "q_adhoc"
    if options is None:
        item: dict[str, Any] = {
            "id": question_id,
            "prompt": prompt,
            "type": "free_text",
            "construct": "adhoc_response",
            "required": True,
        }
    else:
        cleaned = [" ".join(str(option).split()) for option in options]
        cleaned = [option for option in cleaned if option]
        if len(cleaned) < 2:
            raise AdhocSurveyTaskError(
                "a choice question needs at least 2 options; "
                "omit options entirely for a free-text question"
            )
        if len(set(cleaned)) != len(cleaned):
            raise AdhocSurveyTaskError("options must be distinct")
        option_payloads = []
        seen_ids: set[str] = set()
        for index, label in enumerate(cleaned):
            option_id = "{}_{}".format(question_id, _slug(label, max_length=32) or "opt")
            while option_id in seen_ids:
                option_id = "{}_{}".format(option_id, index)
            seen_ids.add(option_id)
            option_payloads.append({"id": option_id, "label": label})
        item = {
            "id": question_id,
            "prompt": prompt,
            "type": "single_choice",
            "construct": "adhoc_response",
            "required": True,
            "options": option_payloads,
        }

    if ask_rationale:
        item["askRationale"] = True

    return {
        "schemaVersion": "1.0",
        "id": questionnaire_id,
        "title": title or _title_from_question(prompt),
        "description": description
        or "Ad-hoc question asked at runtime. Not a benchmark-anchored instrument.",
        "questions": [item],
    }


def build_reporting(
    *,
    segment_dimensions: Iterable[str] = DEFAULT_SEGMENT_DIMENSIONS,
    free_text: bool,
) -> dict[str, Any]:
    """Build reporting.json: the answer distribution, crosstabbed by segment."""
    dimensions = [str(dimension).strip() for dimension in segment_dimensions]
    dimensions = [dimension for dimension in dimensions if dimension]
    distributions: list[dict[str, Any]] = [
        {
            "id": "question_response.response_by_segment",
            "facetKey": "response",
            "title": "Answer distribution by persona segment",
            "groupByPersonaDimensions": dimensions,
        }
    ]
    rule: dict[str, Any] = {
        "match": {"contextType": "question_response"},
        "distributions": distributions,
    }
    if free_text:
        # A free-text answer has no options to count, so the distribution is
        # over themes the summarizer finds rather than over option ids.
        rule["summaryAnalyses"] = [
            {
                "id": "adhoc_response.themes",
                "title": "Themes in free-text answers",
                "targetFacetKey": "response",
                "groupByMode": "none",
                "summaryKind": "llm_bucket_summary",
            }
        ]
    return {"schemaVersion": "1.0", "contextRules": [rule]}


def build_persona_strategy(
    *,
    sample_size: int,
    us_adults_only: bool = True,
) -> dict[str, Any]:
    """Build persona_strategy.json.

    Mirrors ``survey_us-economic-pressure``: the schema has no country
    dimension, so ``region = North America`` plus an adult age filter is the
    closest available proxy for "US adults". A cohort raked by
    ``rake_us_adults.py`` is passed by id instead and overrides this.
    """
    strategy: dict[str, Any] = {
        "schemaVersion": "1.0",
        "sources": [],
        "dimensionFilters": {},
        "sampling": {
            "mode": "stratified",
            "fields": ["age_bracket"],
            "allocation": "proportional",
            "sampleSize": int(sample_size),
        },
    }
    if us_adults_only:
        strategy["dimensionFilters"] = {
            "region": ["North America"],
            "age_bracket": [
                "18-24",
                "25-34",
                "35-44",
                "45-54",
                "55-64",
                "65-74",
                "65+",
                "75-84",
                "85+",
            ],
        }
    return strategy


def _verifier_source_dir(repo_root: Path) -> Path:
    path = repo_root / "application" / "task-spec" / "survey" / "verifier"
    if not path.is_dir():
        raise AdhocSurveyTaskError(
            "generic survey verifier missing at {}".format(path)
        )
    return path


def materialize_adhoc_survey_task(
    *,
    repo_root: Path,
    question: str,
    options: Sequence[str] | None = None,
    title: str = "",
    context_note: str = "",
    sample_size: int = 1000,
    segment_dimensions: Iterable[str] = DEFAULT_SEGMENT_DIMENSIONS,
    ask_rationale: bool = False,
    overwrite: bool = False,
) -> AdhocSurveyTask:
    """Write a runnable survey task folder for one ad-hoc question."""
    prompt = " ".join(str(question).split())
    if not prompt:
        raise AdhocSurveyTaskError("question must not be empty")

    option_list = list(options) if options is not None else None
    identity = {
        "question": prompt,
        "options": option_list,
        "askRationale": bool(ask_rationale),
    }
    digest = _digest(identity)
    folder_name = "{}{}".format(ADHOC_TASK_PREFIX, digest)
    questionnaire_id = "adhoc_{}_v1".format(digest)
    task_path = "application/tasks/{}".format(folder_name)
    task_dir = repo_root / "application" / "tasks" / folder_name

    questionnaire = build_questionnaire(
        question=prompt,
        options=option_list,
        questionnaire_id=questionnaire_id,
        title=title,
        ask_rationale=ask_rationale,
    )

    if task_dir.exists() and not overwrite:
        return AdhocSurveyTask(
            task_path=task_path,
            task_dir=task_dir,
            questionnaire_id=questionnaire_id,
            folder_name=folder_name,
            questionnaire=questionnaire,
            reused=True,
        )

    if task_dir.exists():
        shutil.rmtree(task_dir)

    resolved_title = questionnaire["title"]
    free_text = questionnaire["questions"][0]["type"] == "free_text"

    # Write to a staging dir and move into place, so a crash midway never
    # leaves a half-written folder the task registry would then discover.
    staging = task_dir.parent / ".{}.partial".format(folder_name)
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "input").mkdir(parents=True)
    (staging / "tests").mkdir(parents=True)

    tags = ", ".join(
        '"{}"'.format(tag) for tag in ("ad-hoc", "generated", "single question")
    )
    # Playground derives a task's display title from [task].name, not from the
    # instruction heading, so the question has to live in the name or every
    # generated task shows up in the picker as "Adhoc 06599d5ceb1e".
    (staging / "task.toml").write_text(
        _TASK_TOML_TEMPLATE.format(
            task_name=_task_name_for(prompt, digest),
            domain="general",
            tags=tags,
        ),
        encoding="utf-8",
    )

    (staging / "instruction.md").write_text(
        _INSTRUCTION_TEMPLATE.format(
            title=resolved_title,
            lead="We're asking one question. There is no right answer.",
        ),
        encoding="utf-8",
    )

    extra = ""
    if context_note.strip():
        extra = "{}\n\n".format(context_note.strip())
    (staging / "input" / "context.md").write_text(
        _CONTEXT_TEMPLATE.format(extra=extra), encoding="utf-8"
    )

    (staging / "input" / "questionnaire.yaml").write_text(
        yaml.safe_dump(questionnaire, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    (staging / "reporting.json").write_text(
        json.dumps(
            build_reporting(
                segment_dimensions=segment_dimensions, free_text=free_text
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (staging / "persona_strategy.json").write_text(
        json.dumps(build_persona_strategy(sample_size=sample_size), indent=2) + "\n",
        encoding="utf-8",
    )

    verifier_dir = _verifier_source_dir(repo_root)
    for name in ("test_state.py", "test.sh", "verifier_env.sh"):
        shutil.copy2(verifier_dir / name, staging / "tests" / name)
    (staging / "tests" / "test.sh").chmod(0o755)

    (staging / "README.md").write_text(
        "# {title}\n\n"
        "Generated by `adhoc_survey_task.py` from a question asked at runtime.\n"
        "Do not edit by hand — regenerate instead, or copy this folder to a\n"
        "`survey_*` name if you want to keep and version the instrument.\n\n"
        "**Question**\n\n> {question}\n\n"
        "## What this task is not\n\n"
        "The wording here has not been matched against a published instrument,\n"
        "so its marginals cannot be checked against a real benchmark the way\n"
        "`survey_us-economic-pressure` can. Read the output as a simulated\n"
        "distribution, not as a survey estimate.\n".format(
            title=resolved_title, question=prompt
        ),
        encoding="utf-8",
    )

    staging.rename(task_dir)

    return AdhocSurveyTask(
        task_path=task_path,
        task_dir=task_dir,
        questionnaire_id=questionnaire_id,
        folder_name=folder_name,
        questionnaire=questionnaire,
        reused=False,
    )


def list_adhoc_survey_tasks(*, repo_root: Path) -> list[str]:
    """Return task paths for every materialized ad-hoc survey task."""
    tasks_dir = repo_root / "application" / "tasks"
    if not tasks_dir.is_dir():
        return []
    return [
        "application/tasks/{}".format(child.name)
        for child in sorted(tasks_dir.iterdir())
        if child.is_dir() and child.name.startswith(ADHOC_TASK_PREFIX)
    ]


def remove_adhoc_survey_task(*, repo_root: Path, folder_name: str) -> bool:
    """Delete one generated ad-hoc task folder. Refuses anything else."""
    if not folder_name.startswith(ADHOC_TASK_PREFIX):
        raise AdhocSurveyTaskError(
            "refusing to remove {}: not a generated ad-hoc task".format(folder_name)
        )
    task_dir = repo_root / "application" / "tasks" / folder_name
    if not task_dir.is_dir():
        return False
    shutil.rmtree(task_dir)
    return True
