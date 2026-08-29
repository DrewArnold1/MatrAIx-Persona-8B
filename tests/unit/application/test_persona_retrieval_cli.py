"""Tests for CLI persona retrieval helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "application" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO_ROOT / "application" / "playground") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "application" / "playground"))

from persona_retrieval import (  # noqa: E402
    build_retrieval_plan,
    parse_filter_args,
    parse_filters_json,
    retrieve_personas,
)


def test_parse_filter_args_multi_value() -> None:
    assert parse_filter_args(
        ["age_bracket=25-34,35-44", "life_stage=Mid-life"]
    ) == {
        "age_bracket": ["25-34", "35-44"],
        "life_stage": ["Mid-life"],
    }


def test_parse_filters_json() -> None:
    assert parse_filters_json('{"age_bracket":["25-34"],"region":"North America"}') == {
        "age_bracket": ["25-34"],
        "region": ["North America"],
    }


def test_build_retrieval_plan_applies_task_strategy(tmp_path: Path) -> None:
    task_dir = tmp_path / "application" / "tasks" / "demo-task"
    task_dir.mkdir(parents=True)
    (task_dir / "persona_strategy.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "pool": "persona/datasets/matraix-persona-dev-sample",
                "sources": ["wiki"],
                "dimensionFilters": {"age_bracket": ["25-34", "35-44"]},
                "sampling": {
                    "mode": "stratified",
                    "fields": ["age_bracket"],
                    "allocation": "equalTotal",
                    "sampleSize": 4,
                },
                "seed": 7,
            }
        ),
        encoding="utf-8",
    )

    plan = build_retrieval_plan(
        task_path="application/tasks/demo-task",
        repo_root=tmp_path,
        default_pool="persona/datasets/other",
    )
    assert plan.persona_pool == "persona/datasets/matraix-persona-dev-sample"
    assert plan.sources == ["wiki"]
    assert plan.dimension_filters == {"age_bracket": ["25-34", "35-44"]}
    assert plan.stratify_fields == ["age_bracket"]
    assert plan.sample_size == 4
    assert plan.seed == 7


def test_cli_filters_override_strategy_keys(tmp_path: Path) -> None:
    task_dir = tmp_path / "application" / "tasks" / "demo-task"
    task_dir.mkdir(parents=True)
    (task_dir / "persona_strategy.json").write_text(
        json.dumps(
            {
                "dimensionFilters": {
                    "age_bracket": ["25-34"],
                    "region": ["North America"],
                },
                "sources": ["wiki"],
            }
        ),
        encoding="utf-8",
    )

    plan = build_retrieval_plan(
        task_path="application/tasks/demo-task",
        repo_root=tmp_path,
        default_pool="persona/datasets/matraix-persona-dev-sample",
        filters={"age_bracket": ["45-54"]},
        sources=["amazon"],
        sample_size=2,
        seed=1,
        stratify_fields=[],
    )
    assert plan.dimension_filters == {
        "age_bracket": ["45-54"],
        "region": ["North America"],
    }
    assert plan.sources == ["amazon"]
    assert plan.stratify_fields == []


def test_retrieve_personas_filters_dev_sample() -> None:
    plan = build_retrieval_plan(
        task_path="application/tasks/example-survey_product-feedback",
        repo_root=REPO_ROOT,
        default_pool="persona/datasets/matraix-persona-dev-sample",
        dataset="persona/datasets/matraix-persona-dev-sample",
        sample_size=3,
        seed=42,
        filters={"age_bracket": ["25-34"]},
        stratify_fields=[],
        use_strategy=False,
    )
    result = retrieve_personas(
        plan,
        repo_root=REPO_ROOT,
        task_path="application/tasks/example-survey_product-feedback",
    )
    assert len(result.persona_ids) == 3
    assert result.matched_count >= 3
    assert result.persona_pool.endswith("matraix-persona-dev-sample")


def test_explicit_ids_on_a_yaml_pool_are_passed_through(tmp_path: Path) -> None:
    """A pool that already has persona YAML needs no materialization step."""
    plan = build_retrieval_plan(
        task_path="application/tasks/example-survey_product-feedback",
        repo_root=REPO_ROOT,
        default_pool="persona/datasets/matraix-persona-dev-sample",
        dataset="persona/datasets/matraix-persona-dev-sample",
        persona_ids=["persona-a", "persona-b"],
        seed=42,
        use_strategy=False,
    )
    result = retrieve_personas(plan, repo_root=REPO_ROOT)
    assert result.persona_ids == ["persona-a", "persona-b"]
    assert result.persona_pool == "persona/datasets/matraix-persona-dev-sample"


def test_explicit_ids_on_the_1m_root_are_materialized(monkeypatch, tmp_path: Path) -> None:
    """Hand-picked 1M ids must become a cohort before the job is built.

    The 1M root holds Parquet, not the per-persona YAML plus manifest.json that
    load_manifest and Harbor read. Returning the root unchanged made
    resolve_persona_entries fail with "unknown persona", which is how a raked
    cohort could not be launched at all.
    """
    calls: dict[str, object] = {}

    def fake_materialize(*, repo_root, persona_ids, seed):
        calls["persona_ids"] = list(persona_ids)
        calls["seed"] = seed
        return {
            "pool": "persona/datasets/matraix-persona-1m/cohorts/cohort-abc123",
            "personaIds": list(persona_ids),
        }

    from backend.service import persona_1m_pool

    monkeypatch.setattr(
        persona_1m_pool, "materialize_production_1m_persona_ids", fake_materialize
    )

    plan = build_retrieval_plan(
        task_path="application/tasks/survey_us-economic-pressure",
        repo_root=REPO_ROOT,
        default_pool="persona/datasets/matraix-persona-1m",
        dataset="persona/datasets/matraix-persona-1m",
        persona_ids=["wiki-aaaaaaaaaaaa", "gss-bbbbbbbbbbbb"],
        seed=42,
        use_strategy=False,
    )
    result = retrieve_personas(plan, repo_root=REPO_ROOT)

    assert result.persona_pool == "persona/datasets/matraix-persona-1m/cohorts/cohort-abc123"
    assert result.persona_ids == ["wiki-aaaaaaaaaaaa", "gss-bbbbbbbbbbbb"]
    assert calls["persona_ids"] == ["wiki-aaaaaaaaaaaa", "gss-bbbbbbbbbbbb"]
    assert calls["seed"] == 42


def test_an_already_materialized_cohort_is_not_re_materialized(monkeypatch) -> None:
    """Only the bare 1M root needs materializing; a cohort path is already YAML."""
    from backend.service import persona_1m_pool

    def explode(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("a cohort pool must not be materialized again")

    monkeypatch.setattr(
        persona_1m_pool, "materialize_production_1m_persona_ids", explode
    )

    cohort = "persona/datasets/matraix-persona-1m/cohorts/cohort-abc123"
    plan = build_retrieval_plan(
        task_path="application/tasks/survey_us-economic-pressure",
        repo_root=REPO_ROOT,
        default_pool=cohort,
        dataset=cohort,
        persona_ids=["wiki-aaaaaaaaaaaa"],
        seed=42,
        use_strategy=False,
    )
    result = retrieve_personas(plan, repo_root=REPO_ROOT)
    assert result.persona_pool == cohort
