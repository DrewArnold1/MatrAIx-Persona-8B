from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

from harbor.models.task.paths import TaskPaths
import tomllib


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SURVEY = ROOT / "application/tasks/example-survey_product-feedback"
EXAMPLE_CHAT = ROOT / "application/tasks/example-chat-api_support_chatbot"
TASK_SPEC_ROOT = ROOT / "application/task-spec"


def test_example_survey_task_metadata_is_clean() -> None:
    task_text = (EXAMPLE_SURVEY / "task.toml").read_text(encoding="utf-8")
    task = tomllib.loads(task_text)

    assert task["task"]["name"] == "application/product-feedback"
    assert task["metadata"]["type"] == "survey"
    assert "matraix/" not in task_text.lower()

    readme = (EXAMPLE_SURVEY / "README.md").read_text(encoding="utf-8")
    assert "bench-dev-2000" not in readme


def test_example_survey_verifier_accepts_minimal_valid_result(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "survey_result.json").write_text(
        json.dumps(
            {
                "instrument": {"id": "smoke", "title": "Smoke survey"},
                "answers": [
                    {
                        "questionId": "q1",
                        "value": 4,
                        "rationale": "Fits the assigned persona.",
                        "confidence": 0.8,
                    }
                ],
                "trajectory": [
                    {
                        "timestamp": "2026-06-24T00:00:00Z",
                        "actor": "user",
                        "action": "answer_question",
                        "context": {"questionId": "q1"},
                        "outcome": {"questionId": "q1", "value": 4},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    verifier_path = EXAMPLE_SURVEY / "tests/test_state.py"
    spec = importlib.util.spec_from_file_location("example_survey_test_state", verifier_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    verifier_dir = tmp_path / "verifier"
    verifier_dir.mkdir()
    os.environ["HARBOR_VERIFIER_DIR"] = str(verifier_dir)
    module.OUTPUT_DIR = output_dir
    module.RESULT_PATH = output_dir / "survey_result.json"
    assert module.main() == 0
    payload = json.loads((verifier_dir / "structured_output.json").read_text(encoding="utf-8"))
    assert payload["sourceArtifacts"]["surveyResult"] == "/app/output/survey_result.json"


def test_example_chat_task_metadata_is_clean() -> None:
    task_text = (EXAMPLE_CHAT / "task.toml").read_text(encoding="utf-8")
    task = tomllib.loads(task_text)

    assert task["task"]["name"] == "application/api-support-chatbot"
    assert task["metadata"]["type"] == "chatbot"
    assert task["metadata"]["domain"] == "commerce-retail"
    assert "matraix/" not in task_text.lower()

    readme = (EXAMPLE_CHAT / "README.md").read_text(encoding="utf-8")
    assert "example-chat-mcp_support_chatbot" in readme


def test_example_chat_verifier_accepts_minimal_valid_result(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    messages = [
        {"role": "customer", "content": "Where is my package for order 4521?"},
        {
            "role": "support",
            "content": "Order #4521 is still in transit. Is the shipping address still correct?",
        },
        {"role": "customer", "content": "Yes, the address is correct."},
        {
            "role": "support",
            "content": "Thanks for confirming. Delivery is expected within 1-2 business days.",
        },
    ]
    (output_dir / "transcript.json").write_text(
        json.dumps({"messages": messages}),
        encoding="utf-8",
    )
    (output_dir / "user_feedback.json").write_text(
        json.dumps(
            {
                "needConstraintSatisfaction": "yes",
                "personalPreferenceSatisfaction": "partially",
                "overallExperienceRating": 8,
                "reason": "Support confirmed tracking for order 4521.",
                "askedUsefulClarificationQuestions": True,
            }
        ),
        encoding="utf-8",
    )

    verifier_path = EXAMPLE_CHAT / "tests/test_state.py"
    spec = importlib.util.spec_from_file_location(
        "example_chat_test_state", verifier_path
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    verifier_dir = tmp_path / "verifier"
    verifier_dir.mkdir()
    os.environ["HARBOR_VERIFIER_DIR"] = str(verifier_dir)
    module.OUTPUT_DIR = output_dir
    module.TRANSCRIPT_PATH = output_dir / "transcript.json"
    module.FEEDBACK_PATH = output_dir / "user_feedback.json"
    assert module.main() == 0
    payload = json.loads((verifier_dir / "structured_output.json").read_text(encoding="utf-8"))
    assert payload["sourceArtifacts"]["transcript"] == "/app/output/transcript.json"


def test_example_chat_sidecar_contract() -> None:
    from harbor.environments.compose_materialize import resolve_task_environments_path

    task_paths = TaskPaths.from_task_dir(EXAMPLE_CHAT)
    repo_root = task_paths._find_repository_root()
    assert repo_root is not None
    raw = tomllib.loads((EXAMPLE_CHAT / "task.toml").read_text(encoding="utf-8"))
    local_compose = raw["environment"]["local_compose"]
    sidecar_root = resolve_task_environments_path(repo_root, local_compose)
    server_path = sidecar_root / "support-api" / "server.py"
    compose_path = sidecar_root / "docker-compose.yaml"

    assert server_path.is_file()
    assert compose_path.is_file()
    server_source = server_path.read_text(encoding="utf-8")
    assert '@app.post("/v1/messages")' in server_source
    assert '@app.get("/v1/conversation")' in server_source
    assert "def _bot_reply" in server_source
    # Shared-sidecar batches must scope transcript state by session (#37).
    assert "_sessions" in server_source
    assert "sessionId" in server_source

    chatbot_yaml = (EXAMPLE_CHAT / "input" / "chatbot.yaml").read_text(encoding="utf-8")
    assert "sessionIdField: sessionId" in chatbot_yaml

    test_sh = (EXAMPLE_CHAT / "tests" / "test.sh").read_text(encoding="utf-8")
    # Production verifier must invoke main() so structured_output.json is written.
    assert 'python3 "${TESTS_DIR}/test_state.py"' in test_sh
    assert "pytest --ctrf" not in test_sh
    assert "uvx" not in test_sh


def test_application_task_spec_manifest_uses_clean_task_paths() -> None:
    manifest = json.loads((TASK_SPEC_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schemaVersion"] == "application-task-spec-v1"
    assert set(manifest["applicationTypes"]) == {
        "survey",
        "chatbot",
        "web",
        "os-app",
    }
    assert manifest["applicationTypes"]["survey"]["canonicalTask"] == (
        "application/tasks/example-survey_product-feedback"
    )
    assert manifest["applicationTypes"]["chatbot"]["canonicalTask"] == (
        "application/tasks/example-chat-api_support_chatbot"
    )
    assert manifest["applicationTypes"]["web"]["canonicalTask"] == (
        "application/tasks/example-web-playwright_quote-choice"
    )
    assert manifest["applicationTypes"]["os-app"]["canonicalTask"] == (
        "application/tasks/example-computer-use-ios_photo-access-review"
    )


def test_application_task_spec_docs_cover_each_protocol() -> None:
    for dirname in ("survey", "chatbot", "web"):
        doc = TASK_SPEC_ROOT / dirname / "README.md"
        assert doc.is_file(), doc
        text = doc.read_text(encoding="utf-8")
        assert "Task instruction" in text
        assert "Interaction protocol" in text
        assert "Evaluation contract" in text
        assert "applications/tasks/" not in text

    os_app_doc = TASK_SPEC_ROOT / "os-app" / "README.md"
    assert os_app_doc.is_file()
    os_app_text = os_app_doc.read_text(encoding="utf-8")
    assert "evaluation and reporting contract" in os_app_text.lower()


def _iter_application_task_dirs() -> list[Path]:
    tasks_root = ROOT / "application" / "tasks"
    return sorted(
        path
        for path in tasks_root.iterdir()
        if path.is_dir() and (path / "task.toml").is_file()
    )


def test_every_application_task_has_valid_persona_strategy() -> None:
    """CI gate: persona_strategy.json is required and must declare a cohort."""
    from backend.service.persona_strategy import validate_persona_strategy_file

    task_dirs = _iter_application_task_dirs()
    assert task_dirs, "expected at least one application task under application/tasks/"

    failures: list[str] = []
    for task_dir in task_dirs:
        failures.extend(validate_persona_strategy_file(task_dir, require_cohort=True))

    assert not failures, "persona_strategy.json validation failed:\n- " + "\n- ".join(
        failures
    )


US_ECON_SURVEY = ROOT / "application/tasks/survey_us-economic-pressure"


def _load_us_econ_verifier(output_dir: Path):
    """Import the verifier fresh so its OUTPUT_DIR-derived paths resolve."""
    spec = importlib.util.spec_from_file_location(
        "us_econ_test_state_{}".format(abs(hash(str(output_dir)))),
        US_ECON_SURVEY / "tests/test_state.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_us_econ_fidelity_resolves_persona_without_app_mount(
    tmp_path: Path, monkeypatch
) -> None:
    """The fidelity check must fire on native host profiles.

    Regression: PERSONA_CANDIDATES only listed MATRIX_PERSONA_PATH (set by
    nothing) and /app/input/persona.yaml (absent unless Docker mounts it), so
    the json_survey host profile silently produced zero survey.fidelity.*
    facets on every trial instead of the documented sparse-but-nonzero rate.
    """
    output_dir = tmp_path / "app" / "output"
    input_dir = tmp_path / "app" / "input"
    output_dir.mkdir(parents=True)
    input_dir.mkdir(parents=True)
    (input_dir / "persona.yaml").write_text(
        "dimensions:\n"
        "  age_bracket: 45-54\n"
        "  demo_employment_status: Full-time\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARBOR_OUTPUT_DIR", str(output_dir))
    monkeypatch.delenv("MATRIX_PERSONA_PATH", raising=False)

    module = _load_us_econ_verifier(output_dir)
    facets = module._fidelity_facets(
        {
            "q_self_age": "q_self_age_45_54",
            "q_self_employment": "q_self_employment_gig",
        }
    )

    by_key = {f["key"]: f["value"] for f in facets}
    assert by_key == {
        "fidelity_age_bracket": True,
        "fidelity_demo_employment_status": False,
    }


def test_us_econ_fidelity_skips_dimensions_the_persona_lacks(
    tmp_path: Path, monkeypatch
) -> None:
    """Off-schema and absent dimensions stay uncoded rather than guessed.

    '65+' is carried by a large slice of the pool but is not a schema age
    bracket, so it must not be mapped onto 65-74/75-84/85+.
    """
    output_dir = tmp_path / "app" / "output"
    input_dir = tmp_path / "app" / "input"
    output_dir.mkdir(parents=True)
    input_dir.mkdir(parents=True)
    (input_dir / "persona.yaml").write_text(
        "dimensions:\n  age_bracket: 65+\n  demo_employment_status: Retired\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARBOR_OUTPUT_DIR", str(output_dir))
    monkeypatch.delenv("MATRIX_PERSONA_PATH", raising=False)

    module = _load_us_econ_verifier(output_dir)
    facets = module._fidelity_facets(
        {
            "q_self_age": "q_self_age_65_74",
            "q_self_employment": "q_self_employment_retired",
        }
    )

    assert [f["key"] for f in facets] == ["fidelity_demo_employment_status"]
    assert facets[0]["value"] is True
