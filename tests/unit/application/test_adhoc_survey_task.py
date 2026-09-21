"""Tests for ad-hoc survey task materialization.

The point of these: a task generated from a runtime question has to be
indistinguishable, downstream, from one someone authored by hand. So the
assertions are mostly about the contract the rest of the pipeline reads —
the questionnaire id the registry discovers, the option ids the verifier
validates against, the reporting directives the aggregator resolves.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (
    REPO_ROOT / "application" / "playground",
    REPO_ROOT / "packages" / "playground" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from backend.service.adhoc_survey_task import (  # noqa: E402
    ADHOC_TASK_PREFIX,
    AdhocSurveyTaskError,
    build_persona_strategy,
    build_questionnaire,
    build_reporting,
    list_adhoc_survey_tasks,
    materialize_adhoc_survey_task,
    remove_adhoc_survey_task,
)


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """A repo root holding only what the materializer reads."""
    verifier_src = REPO_ROOT / "application" / "task-spec" / "survey" / "verifier"
    verifier_dst = tmp_path / "application" / "task-spec" / "survey" / "verifier"
    verifier_dst.mkdir(parents=True)
    for name in ("test_state.py", "test.sh", "verifier_env.sh"):
        (verifier_dst / name).write_text(
            (verifier_src / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "application" / "tasks").mkdir(parents=True)
    return tmp_path


def test_choice_question_builds_valid_option_ids() -> None:
    questionnaire = build_questionnaire(
        question="Is the country on the right track?",
        options=["Right track", "Wrong track", "Not sure"],
        questionnaire_id="adhoc_deadbeef_v1",
    )
    question = questionnaire["questions"][0]
    assert question["type"] == "single_choice"
    option_ids = [option["id"] for option in question["options"]]
    assert option_ids == [
        "q_adhoc_right-track",
        "q_adhoc_wrong-track",
        "q_adhoc_not-sure",
    ]
    # Ids must be unique: the verifier validates answers against them.
    assert len(set(option_ids)) == len(option_ids)


def test_question_without_options_is_free_text() -> None:
    """No options means free text, not invented options.

    Generating answer options from the question puts the framing being
    measured into the instrument, which is the one thing a survey must not do.
    """
    questionnaire = build_questionnaire(
        question="What worries you most about the economy?",
        questionnaire_id="adhoc_deadbeef_v1",
    )
    assert questionnaire["questions"][0]["type"] == "free_text"
    assert "options" not in questionnaire["questions"][0]


def test_single_option_is_rejected() -> None:
    with pytest.raises(AdhocSurveyTaskError, match="at least 2 options"):
        build_questionnaire(
            question="Do you agree?",
            options=["Yes"],
            questionnaire_id="adhoc_deadbeef_v1",
        )


def test_duplicate_options_are_rejected() -> None:
    with pytest.raises(AdhocSurveyTaskError, match="distinct"):
        build_questionnaire(
            question="Do you agree?",
            options=["Yes", "Yes"],
            questionnaire_id="adhoc_deadbeef_v1",
        )


def test_empty_question_is_rejected() -> None:
    with pytest.raises(AdhocSurveyTaskError, match="must not be empty"):
        build_questionnaire(question="   ", questionnaire_id="adhoc_deadbeef_v1")


def test_free_text_reporting_adds_theme_summary() -> None:
    reporting = build_reporting(segment_dimensions=["age_bracket"], free_text=True)
    rule = reporting["contextRules"][0]
    assert rule["match"]["contextType"] == "question_response"
    assert rule["summaryAnalyses"][0]["summaryKind"] == "llm_bucket_summary"


def test_choice_reporting_has_no_theme_summary() -> None:
    reporting = build_reporting(segment_dimensions=["age_bracket"], free_text=False)
    assert "summaryAnalyses" not in reporting["contextRules"][0]


def test_persona_strategy_filters_to_adults() -> None:
    strategy = build_persona_strategy(sample_size=1000)
    age_values = strategy["dimensionFilters"]["age_bracket"]
    assert "18-24" in age_values
    # Minors carry valid age codes and would inflate every margin denominator.
    assert not any(value.startswith(("0-", "5-", "13-")) for value in age_values)
    assert strategy["sampling"]["sampleSize"] == 1000


def test_materialize_writes_a_complete_task(fake_repo: Path) -> None:
    task = materialize_adhoc_survey_task(
        repo_root=fake_repo,
        question="Is the country on the right track?",
        options=["Right track", "Wrong track"],
        sample_size=250,
    )
    assert task.reused is False
    assert task.folder_name.startswith(ADHOC_TASK_PREFIX)

    task_dir = task.task_dir
    for relative in (
        "task.toml",
        "instruction.md",
        "README.md",
        "reporting.json",
        "persona_strategy.json",
        "input/questionnaire.yaml",
        "input/context.md",
        "tests/test_state.py",
        "tests/test.sh",
        "tests/verifier_env.sh",
    ):
        assert (task_dir / relative).is_file(), relative

    # The registry finds a questionnaire by reading this id out of the YAML.
    questionnaire = yaml.safe_load((task_dir / "input" / "questionnaire.yaml").read_text())
    assert questionnaire["id"] == task.questionnaire_id
    assert len(questionnaire["questions"]) == 1

    # task.toml must name the shared survey environment, or the job will not run.
    task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
    assert 'definition = "application/shared-survey-form"' in task_toml
    assert 'type = "survey"' in task_toml

    reporting = json.loads((task_dir / "reporting.json").read_text(encoding="utf-8"))
    assert reporting["contextRules"][0]["distributions"][0]["facetKey"] == "response"


def test_test_sh_is_executable(fake_repo: Path) -> None:
    task = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Are you registered to vote?", options=["Yes", "No"]
    )
    mode = (task.task_dir / "tests" / "test.sh").stat().st_mode
    assert mode & 0o111, "verifier entrypoint must be executable"


def test_same_question_reuses_one_folder(fake_repo: Path) -> None:
    first = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Same question?", options=["Yes", "No"]
    )
    second = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Same   question?", options=["Yes", "No"]
    )
    assert second.reused is True
    assert second.folder_name == first.folder_name
    assert len(list_adhoc_survey_tasks(repo_root=fake_repo)) == 1


def test_different_options_make_a_different_task(fake_repo: Path) -> None:
    """Options are part of the instrument, so they are part of its identity."""
    first = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Right track?", options=["Yes", "No"]
    )
    second = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Right track?", options=["Yes", "No", "Not sure"]
    )
    assert first.folder_name != second.folder_name


def test_overwrite_rewrites_the_folder(fake_repo: Path) -> None:
    task = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Right track?", options=["Yes", "No"]
    )
    stray = task.task_dir / "stale.txt"
    stray.write_text("leftover", encoding="utf-8")
    again = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Right track?", options=["Yes", "No"], overwrite=True
    )
    assert again.reused is False
    assert not stray.exists()


def test_no_partial_folder_is_left_behind(fake_repo: Path) -> None:
    """A folder only appears once it is complete.

    The registry globs application/tasks/survey_*, so a half-written folder
    would be discovered and then fail to load.
    """
    materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Right track?", options=["Yes", "No"]
    )
    tasks_dir = fake_repo / "application" / "tasks"
    assert not any(child.name.endswith(".partial") for child in tasks_dir.iterdir())


def test_remove_refuses_non_adhoc_folders(fake_repo: Path) -> None:
    with pytest.raises(AdhocSurveyTaskError, match="not a generated ad-hoc task"):
        remove_adhoc_survey_task(
            repo_root=fake_repo, folder_name="survey_us-economic-pressure"
        )


def test_remove_deletes_a_generated_folder(fake_repo: Path) -> None:
    task = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Right track?", options=["Yes", "No"]
    )
    assert remove_adhoc_survey_task(repo_root=fake_repo, folder_name=task.folder_name)
    assert list_adhoc_survey_tasks(repo_root=fake_repo) == []
    assert remove_adhoc_survey_task(repo_root=fake_repo, folder_name=task.folder_name) is False


def test_context_note_reaches_the_respondent_context(fake_repo: Path) -> None:
    task = materialize_adhoc_survey_task(
        repo_root=fake_repo,
        question="Right track?",
        options=["Yes", "No"],
        context_note="It is October 2026.",
    )
    context = (task.task_dir / "input" / "context.md").read_text(encoding="utf-8")
    assert "It is October 2026." in context


def test_task_name_carries_the_question(fake_repo: Path) -> None:
    """Playground builds the picker's card title from [task].name.

    Without the question in the name, every generated task shows up in the
    task picker as "Adhoc 06599d5ceb1e", which is unusable for choosing one.
    """
    task = materialize_adhoc_survey_task(
        repo_root=fake_repo,
        question="Is the country on the right track?",
        options=["Yes", "No"],
    )
    task_toml = (task.task_dir / "task.toml").read_text(encoding="utf-8")
    assert 'name = "application/ask-is-the-country-on-the-right-track-' in task_toml


def test_task_name_stays_distinct_for_similar_questions(fake_repo: Path) -> None:
    first = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Right track?", options=["Yes", "No"]
    )
    second = materialize_adhoc_survey_task(
        repo_root=fake_repo, question="Right track?", options=["Yes", "No", "Unsure"]
    )
    first_name = (first.task_dir / "task.toml").read_text(encoding="utf-8")
    second_name = (second.task_dir / "task.toml").read_text(encoding="utf-8")
    assert first_name != second_name
