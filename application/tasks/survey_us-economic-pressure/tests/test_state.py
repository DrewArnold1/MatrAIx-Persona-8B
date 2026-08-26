from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - container usually has PyYAML
    yaml = None

OUTPUT_DIR = Path(
    os.environ.get("HARBOR_OUTPUT_DIR")
    or os.environ.get("MATRIX_OUTPUT_DIR")
    or "/app/output"
)
RESULT_PATH = OUTPUT_DIR / "survey_result.json"
EVENT_KEYS = {"timestamp", "actor", "action", "context", "outcome"}

QUESTIONNAIRE_CANDIDATES = (
    Path(os.environ.get("MATRIX_QUESTIONNAIRE_PATH", "/nonexistent")),
    Path("/app/input/questionnaire.yaml"),
    Path("/app/input/input/questionnaire.yaml"),
    Path(__file__).resolve().parent.parent / "input" / "questionnaire.yaml",
)
PERSONA_CANDIDATES = (
    Path(os.environ.get("MATRIX_PERSONA_PATH", "/nonexistent")),
    Path("/app/input/persona.yaml"),
)

# Ordered scales for the two parallel perception items. The construct of
# interest is the GAP between them, which US data shows is large and positive
# (people rate their own finances well above the national economy). Both are
# mapped to 0..1 so the difference is comparable.
OWN_FINANCES_SCALE = {
    "q_own_finances_struggling": 0.0,
    "q_own_finances_just_getting_by": 1 / 3,
    "q_own_finances_doing_ok": 2 / 3,
    "q_own_finances_thriving": 1.0,
}
NATIONAL_ECONOMY_SCALE = {
    "q_national_economy_poor": 0.0,
    "q_national_economy_fair": 1 / 3,
    "q_national_economy_good": 2 / 3,
    "q_national_economy_excellent": 1.0,
}
# SHED-style "could cover a $400 expense with cash or equivalent".
CASH_EQUIVALENT_COVERAGE = {
    "q_emergency_400_cash",
    "q_emergency_400_card_full",
}
NONE_OPTIONS = {"q_cutbacks_none", "q_deferred_none"}

# Persona dimension -> option id, for the fidelity check.
AGE_OPTION_FOR_DIMENSION = {
    "18-24": "q_self_age_18_24",
    "25-34": "q_self_age_25_34",
    "35-44": "q_self_age_35_44",
    "45-54": "q_self_age_45_54",
    "55-64": "q_self_age_55_64",
    "65-74": "q_self_age_65_74",
    "75-84": "q_self_age_75_84",
    "85+": "q_self_age_85_plus",
}
EMPLOYMENT_OPTION_FOR_DIMENSION = {
    "Full-time": "q_self_employment_full_time",
    "Part-time": "q_self_employment_part_time",
    "Self-employed": "q_self_employment_self",
    "Gig / freelance": "q_self_employment_gig",
    "Student": "q_self_employment_student",
    "Unemployed": "q_self_employment_unemployed",
    "Retired": "q_self_employment_retired",
    "Homemaker": "q_self_employment_homemaker",
}


def _verifier_dir() -> Path:
    explicit = os.environ.get("HARBOR_VERIFIER_DIR")
    if explicit:
        path = Path(explicit)
        path.mkdir(parents=True, exist_ok=True)
        return path

    container_default = Path("/logs/verifier")
    try:
        container_default.mkdir(parents=True, exist_ok=True)
        return container_default
    except OSError:
        pass

    raise RuntimeError(
        "HARBOR_VERIFIER_DIR is required when running outside a Harbor trial "
        "container. Point it at jobs/<job>/<trial>/verifier for local harness runs."
    )


def _write_structured_output(payload: dict[str, object]) -> None:
    path = _verifier_dir() / "structured_output.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _first_existing(candidates) -> Path | None:
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _load_yaml(candidates) -> dict:
    path = _first_existing(candidates)
    if path is None or yaml is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _question_types_from_trajectory(trajectory: list[object]) -> dict[str, str]:
    """Map questionId -> questionnaire type from ask_question trajectory events."""
    out: dict[str, str] = {}
    for event in trajectory:
        if not isinstance(event, dict):
            continue
        if str(event.get("action") or "") != "ask_question":
            continue
        context = event.get("context")
        if not isinstance(context, dict):
            continue
        question_id = str(context.get("questionId") or "").strip()
        question_type = str(context.get("questionType") or "").strip().lower()
        if question_id and question_type:
            out[question_id] = question_type
    return out


def _field_kind_for_question(question_type: str | None, value: object) -> str:
    """Kind follows questionnaire type — not string-shape heuristics."""
    qtype = (question_type or "").strip().lower()
    if qtype == "likert":
        return "numerical"
    if qtype in {"single_choice", "multi_choice"}:
        return "categorical"
    if qtype == "free_text":
        return "textual"
    if isinstance(value, bool):
        return "categorical"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "numerical"
    if isinstance(value, list):
        return "categorical"
    if isinstance(value, str):
        text = value.strip()
        if " " in text or "\n" in text or len(text) > 64:
            return "textual"
        return "categorical"
    return "textual"


def _validate_against_instrument(
    answers_by_id: dict[str, object], instrument: dict
) -> tuple[list[str], list[str]]:
    """Check coverage, option validity and scale ranges. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    questions = instrument.get("questions")
    if not isinstance(questions, list) or not questions:
        warnings.append(
            "questionnaire.yaml not readable from the verifier; option ids and "
            "required-question coverage were not checked"
        )
        return errors, warnings

    for question in questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("id") or "").strip()
        if not qid:
            continue
        qtype = str(question.get("type") or "").strip().lower()
        required = bool(question.get("required"))
        if qid not in answers_by_id:
            if required:
                errors.append(f"required question '{qid}' was not answered")
            continue
        value = answers_by_id[qid]
        valid_ids = {
            str(option.get("id"))
            for option in (question.get("options") or [])
            if isinstance(option, dict) and option.get("id")
        }
        if qtype == "single_choice":
            if not isinstance(value, str):
                errors.append(f"'{qid}' must be a single option id string")
            elif valid_ids and value not in valid_ids:
                errors.append(f"'{qid}' answered with unknown option id {value!r}")
        elif qtype == "multi_choice":
            if not isinstance(value, list) or not value:
                errors.append(
                    f"'{qid}' must be a non-empty list of option ids "
                    "(use the explicit 'None of these' option, not an empty list)"
                )
            else:
                unknown = [
                    item
                    for item in value
                    if not isinstance(item, str) or (valid_ids and item not in valid_ids)
                ]
                if unknown:
                    errors.append(f"'{qid}' contains unknown option ids: {unknown}")
                elif len(set(value)) != len(value):
                    warnings.append(f"'{qid}' repeats an option id")
                chosen_none = [item for item in value if item in NONE_OPTIONS]
                if chosen_none and len(value) > 1:
                    warnings.append(
                        f"'{qid}' combines {chosen_none[0]!r} with other selections, "
                        "which is contradictory"
                    )
        elif qtype == "likert":
            low = question.get("minValue")
            high = question.get("maxValue")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"'{qid}' must be a number")
            elif isinstance(low, (int, float)) and isinstance(high, (int, float)):
                if not low <= value <= high:
                    errors.append(f"'{qid}' value {value} is outside {low}..{high}")
        elif qtype == "free_text":
            if not isinstance(value, str) or not value.strip():
                errors.append(f"'{qid}' must be non-empty text")
            elif len(value.split()) < 5:
                warnings.append(f"'{qid}' is unusually short for a free-text answer")
    return errors, warnings


def _derived_facets(answers_by_id: dict[str, object]) -> list[dict[str, object]]:
    """Compute the study's headline constructs so reporting gets them directly."""
    facets: list[dict[str, object]] = []

    own = OWN_FINANCES_SCALE.get(answers_by_id.get("q_own_finances"))  # type: ignore[arg-type]
    national = NATIONAL_ECONOMY_SCALE.get(answers_by_id.get("q_national_economy"))  # type: ignore[arg-type]
    if own is not None and national is not None:
        facets.append(
            {
                "key": "perception_gap",
                "label": "Own finances minus national economy (0-1 scale)",
                "role": "score",
                "kind": "numerical",
                "value": round(own - national, 4),
            }
        )
        facets.append(
            {
                "key": "own_finances_scaled",
                "label": "Own finances rating (0-1)",
                "role": "score",
                "kind": "numerical",
                "value": round(own, 4),
            }
        )
        facets.append(
            {
                "key": "national_economy_scaled",
                "label": "National economy rating (0-1)",
                "role": "score",
                "kind": "numerical",
                "value": round(national, 4),
            }
        )

    emergency = answers_by_id.get("q_emergency_400")
    if isinstance(emergency, str):
        facets.append(
            {
                "key": "covers_400_with_cash",
                "label": "Could cover $400 with cash or equivalent",
                "role": "score",
                "kind": "categorical",
                "value": emergency in CASH_EQUIVALENT_COVERAGE,
            }
        )

    deferred = answers_by_id.get("q_deferred")
    if isinstance(deferred, list):
        medical = {
            "q_deferred_medical",
            "q_deferred_dental",
            "q_deferred_prescription",
            "q_deferred_mental_health",
        }
        facets.append(
            {
                "key": "deferred_any_care",
                "label": "Skipped or delayed any health care over cost",
                "role": "score",
                "kind": "categorical",
                "value": bool(medical.intersection(deferred)),
            }
        )
    return facets


def _fidelity_facets(answers_by_id: dict[str, object]) -> list[dict[str, object]]:
    """Compare self-reported demographics against the persona record.

    A mismatch means the agent drifted out of character, which invalidates the
    substantive answers for that trial. This is a manipulation check, not a
    finding - it is reported, never used to fail the trial, because a wrong
    self-report is a real behaviour worth measuring rather than a broken run.
    """
    persona = _load_yaml(PERSONA_CANDIDATES)
    dimensions = persona.get("dimensions") if isinstance(persona, dict) else None
    if not isinstance(dimensions, dict):
        return []
    facets: list[dict[str, object]] = []
    for dimension, mapping, question_id, label in (
        ("age_bracket", AGE_OPTION_FOR_DIMENSION, "q_self_age", "Age"),
        (
            "demo_employment_status",
            EMPLOYMENT_OPTION_FOR_DIMENSION,
            "q_self_employment",
            "Employment",
        ),
    ):
        expected = mapping.get(dimensions.get(dimension))
        actual = answers_by_id.get(question_id)
        if expected is None or not isinstance(actual, str):
            continue
        facets.append(
            {
                "key": f"fidelity_{dimension}",
                "label": f"{label} self-report matches persona",
                "role": "score",
                "kind": "categorical",
                "value": actual == expected,
            }
        )
    return facets


def main() -> int:
    if not RESULT_PATH.is_file():
        return fail("missing /app/output/survey_result.json")
    try:
        payload = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail("survey_result.json is not valid JSON: {}".format(exc))
    if not isinstance(payload, dict):
        return fail("survey_result.json must contain an object")
    answers = payload.get("answers")
    if not isinstance(answers, list) or not answers:
        return fail("survey_result.answers must be a non-empty list")
    trajectory = payload.get("trajectory")
    if not isinstance(trajectory, list) or not trajectory:
        return fail("survey_result.trajectory must be a non-empty list")
    question_types = _question_types_from_trajectory(trajectory)
    fields: list[dict[str, object]] = []
    contexts: list[dict[str, object]] = []
    numeric_values: list[float] = []
    answers_by_id: dict[str, object] = {}
    for index, answer in enumerate(answers):
        if not isinstance(answer, dict):
            return fail("answers[{}] must be an object".format(index))
        question_id = str(answer.get("questionId", "")).strip()
        if not question_id:
            return fail("answers[{}].questionId is required".format(index))
        if "value" not in answer:
            return fail("answers[{}].value is required".format(index))
        value = answer.get("value")
        if question_id in answers_by_id:
            return fail("duplicate answer for question '{}'".format(question_id))
        answers_by_id[question_id] = value
        question_type = question_types.get(question_id)
        kind = _field_kind_for_question(question_type, value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric_values.append(float(value))
        context_key = "question.{}".format(question_id)
        context_label = str(answer.get("prompt") or question_id)
        facets: list[dict[str, object]] = [
            {
                "key": "response",
                "label": "Selected response",
                "role": "primary",
                "kind": kind,
                "value": value,
            }
        ]
        fields.append(
            {
                "key": "{}.response".format(context_key),
                "label": "Selected response",
                "group": context_key,
                "role": "primary",
                "kind": kind,
                "value": value,
            }
        )
        rationale = str(answer.get("rationale") or "").strip()
        if rationale:
            facets.append(
                {
                    "key": "reason",
                    "label": "Reason",
                    "role": "explanation",
                    "kind": "textual",
                    "value": rationale,
                }
            )
            fields.append(
                {
                    "key": "{}.reason".format(context_key),
                    "label": "Reason",
                    "group": context_key,
                    "role": "explanation",
                    "kind": "textual",
                    "value": rationale,
                }
            )
        confidence = answer.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            facets.append(
                {
                    "key": "confidence",
                    "label": "Confidence",
                    "role": "score",
                    "kind": "numerical",
                    "value": float(confidence),
                }
            )
            fields.append(
                {
                    "key": "{}.confidence".format(context_key),
                    "label": "Confidence",
                    "group": context_key,
                    "role": "score",
                    "kind": "numerical",
                    "value": float(confidence),
                }
            )
        context_payload: dict[str, object] = {
            "key": context_key,
            "label": context_label,
            "contextType": "question_response",
            "facets": facets,
        }
        if question_type:
            context_payload["questionType"] = question_type
        contexts.append(context_payload)
    for index, event in enumerate(trajectory):
        if not isinstance(event, dict):
            return fail("trajectory[{}] must be an object".format(index))
        missing = EVENT_KEYS - set(event)
        if missing:
            return fail(
                "trajectory[{}] missing keys: {}".format(index, ", ".join(sorted(missing)))
            )
        if not isinstance(event.get("context"), dict):
            return fail("trajectory[{}].context must be an object".format(index))
        if not isinstance(event.get("outcome"), dict):
            return fail("trajectory[{}].outcome must be an object".format(index))

    instrument = _load_yaml(QUESTIONNAIRE_CANDIDATES)
    errors, warnings = _validate_against_instrument(answers_by_id, instrument)
    if errors:
        return fail("instrument validation failed:\n  - " + "\n  - ".join(errors))
    for warning in warnings:
        print("warning: {}".format(warning), file=sys.stderr)

    summary_facets: list[dict[str, object]] = [
        {
            "key": "answer_count",
            "label": "Answer count",
            "role": "score",
            "kind": "numerical",
            "value": len(answers),
        },
        {
            "key": "trajectory_event_count",
            "label": "Trajectory event count",
            "role": "score",
            "kind": "numerical",
            "value": len(trajectory),
        },
    ]
    fields.append(
        {
            "key": "survey.summary.answer_count",
            "label": "Answer count",
            "group": "survey.summary",
            "role": "score",
            "kind": "numerical",
            "value": len(answers),
        }
    )
    fields.append(
        {
            "key": "survey.summary.trajectory_event_count",
            "label": "Trajectory event count",
            "group": "survey.summary",
            "role": "score",
            "kind": "numerical",
            "value": len(trajectory),
        }
    )
    if numeric_values:
        summary_facets.append(
            {
                "key": "mean_numeric_answer",
                "label": "Mean numeric answer",
                "role": "score",
                "kind": "numerical",
                "value": round(sum(numeric_values) / len(numeric_values), 4),
            }
        )
        fields.append(
            {
                "key": "survey.summary.mean_numeric_answer",
                "label": "Mean numeric answer",
                "group": "survey.summary",
                "role": "score",
                "kind": "numerical",
                "value": round(sum(numeric_values) / len(numeric_values), 4),
            }
        )
    if warnings:
        summary_facets.append(
            {
                "key": "quality_warnings",
                "label": "Response quality warnings",
                "role": "explanation",
                "kind": "textual",
                "value": "; ".join(warnings),
            }
        )
    contexts.append(
        {
            "key": "survey.summary",
            "label": "Survey summary",
            "contextType": "trial_summary",
            "facets": summary_facets,
        }
    )

    derived = _derived_facets(answers_by_id)
    if derived:
        contexts.append(
            {
                "key": "survey.derived",
                "label": "Derived constructs",
                "contextType": "trial_summary",
                "facets": derived,
            }
        )
        for facet in derived:
            fields.append(
                {
                    "key": "survey.derived.{}".format(facet["key"]),
                    "label": facet["label"],
                    "group": "survey.derived",
                    "role": facet["role"],
                    "kind": facet["kind"],
                    "value": facet["value"],
                }
            )

    fidelity = _fidelity_facets(answers_by_id)
    if fidelity:
        contexts.append(
            {
                "key": "survey.fidelity",
                "label": "Persona fidelity check",
                "contextType": "trial_summary",
                "facets": fidelity,
            }
        )
        for facet in fidelity:
            fields.append(
                {
                    "key": "survey.fidelity.{}".format(facet["key"]),
                    "label": facet["label"],
                    "group": "survey.fidelity",
                    "role": facet["role"],
                    "kind": facet["kind"],
                    "value": facet["value"],
                }
            )

    _write_structured_output(
        {
            "schemaVersion": "1.0",
            "artifactType": "matraix.trial_evaluation",
            "taskType": "survey",
            "presenceCheck": {
                "passed": True,
                "requiredArtifacts": ["survey_result.json"],
                "missingArtifacts": [],
            },
            "sourceArtifacts": {
                "surveyResult": "/app/output/survey_result.json",
            },
            "contexts": contexts,
            "fields": fields,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
