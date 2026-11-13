"""Cross-component invariant: the artifact type the engine grades equals the type the agent is told to submit."""

from __future__ import annotations

import re
import shutil
from importlib import resources
from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.policy.loader import default_dir, load_policy
from ralph.policy.models import ArtifactsPolicy

_SUBMISSION_CALL = re.compile(r"render_artifact_submission\(\s*'([a-z_]+)'")
_SKILL_POINTER = re.compile(
    r"render_optional_artifact_skill_pointer\(\s*'(submit-[a-z-]+-artifact)'"
)


def _matches(pattern: re.Pattern[str], source: str) -> list[str]:
    return [match.group(1) for match in pattern.finditer(source)]


def _contract(drain: str, artifact_type: str) -> dict[str, object]:
    return {"drain": drain, "artifact_type": artifact_type}


def test_two_contracts_for_one_drain_with_different_types_are_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        ArtifactsPolicy.model_validate(
            {
                "artifacts": {
                    "development_commit_review_artifact": _contract("commit", "commit_review"),
                    "stale_artifact": _contract("commit", "stale_review"),
                }
            }
        )
    message = str(caught.value)
    assert "drain 'commit'" in message
    assert "commit_review" in message
    assert "stale_review" in message


def _write_project_policy_with_stale_contract(directory: Path) -> None:
    for name in ("agents.toml", "pipeline.toml"):
        shutil.copy(default_dir() / name, directory / name)
    (directory / "artifacts.toml").write_text(
        "[artifacts.development_commit_stale_artifact]\n"
        'drain = "commit"\n'
        'artifact_type = "stale_review"\n'
        "decision_vocabulary = []\n",
        encoding="utf-8",
    )


def test_stale_project_contract_is_a_loud_error_in_strict_load(tmp_path: Path) -> None:
    _write_project_policy_with_stale_contract(tmp_path)
    with pytest.raises(Exception, match=r"stale_review") as caught:
        load_policy(tmp_path, strict_optional_files=True)
    assert "commit_review" in str(caught.value)


def test_stale_project_contract_never_overrides_the_default_grading_type(
    tmp_path: Path,
) -> None:
    _write_project_policy_with_stale_contract(tmp_path)
    bundle = load_policy(tmp_path)
    for phase in ("development_commit_review", "development_final_commit_review"):
        required = resolve_phase_required_artifact(bundle.pipeline, bundle.artifacts, phase=phase)
        assert required is not None
        assert required.artifact_type == "commit_review"


def test_every_default_phase_grades_the_type_its_prompt_and_skill_name() -> None:
    bundle = load_policy(default_dir())
    templates = resources.files("ralph.prompts") / "templates"
    skills = resources.files("ralph.skills") / "content"
    checked: list[str] = []
    for phase_name, phase_def in bundle.pipeline.phases.items():
        required = resolve_phase_required_artifact(
            bundle.pipeline, bundle.artifacts, phase=phase_name
        )
        if required is None or phase_def.prompt_template is None:
            continue
        source = (templates / phase_def.prompt_template).read_text(encoding="utf-8")
        prompted = _matches(_SUBMISSION_CALL, source)
        if not prompted:
            continue
        assert set(prompted) == {required.artifact_type}, phase_name
        for skill in _matches(_SKILL_POINTER, source):
            if skill == "submit-artifact":
                continue
            assert (skills / f"{skill}.md").is_file(), phase_name
            expected = f"submit-{required.artifact_type.replace('_', '-')}-artifact"
            assert skill == expected, phase_name
        checked.append(phase_name)
    assert {"development_commit_review", "development_final_commit_review"} <= set(checked)
