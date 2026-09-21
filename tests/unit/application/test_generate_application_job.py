"""Tests for application job generation helpers."""

from __future__ import annotations

from pathlib import Path

from matraix.application_job import collect_run_env_exports
from matraix.provider_credentials import export_hint_lines, resolve_provider_credential

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_collect_run_env_exports_survey() -> None:
    exports = collect_run_env_exports(
        trial_profile="json_survey",
        task_path="application/tasks/example-survey_product-feedback",
        repo_root=REPO_ROOT,
    )
    assert exports == [("MATRIX_SURVEY_TASK_PATH", "application/tasks/example-survey_product-feedback")]


def test_collect_run_env_exports_chat() -> None:
    exports = collect_run_env_exports(
        trial_profile="user_sim_chat",
        task_path="application/tasks/chat_meal-planning-nutrition",
        repo_root=REPO_ROOT,
    )
    assert exports == [
        ("MATRIX_CHATBOT_TASK_PATH", "application/tasks/chat_meal-planning-nutrition")
    ]


def test_export_hint_lines_are_provider_aware() -> None:
    assert export_hint_lines("openai/gpt-4o-mini") == ["export OPENAI_API_KEY=..."]
    assert export_hint_lines("dashscope/deepseek-v3.2") == [
        "export DASHSCOPE_API_KEY=..."
    ]
    assert resolve_provider_credential("openai/gpt-4o-mini").env_var != "ANTHROPIC_API_KEY"


def _default_concurrency(**kwargs):
    import sys

    scripts_dir = REPO_ROOT / "application" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from generate_application_job import _default_concurrency as impl

    return impl(**kwargs)


def test_single_trial_runs_serially() -> None:
    assert _default_concurrency(sample_size=1, execution_mode="auto") == 1


def test_cohort_scale_runs_get_parallelism() -> None:
    """A 1000-trial survey run at one trial at a time is hours to days."""
    assert _default_concurrency(sample_size=1000, execution_mode="auto") == 16


def test_small_cohorts_stay_near_serial() -> None:
    assert _default_concurrency(sample_size=4, execution_mode="auto") == 1
    assert _default_concurrency(sample_size=16, execution_mode="auto") == 4


def test_docker_gets_a_lower_ceiling() -> None:
    """Docker trials each hold a container, so parallelism costs memory too."""
    assert _default_concurrency(sample_size=1000, execution_mode="force_docker") == 4
