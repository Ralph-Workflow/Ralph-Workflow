from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.mcp.artifacts.completion_receipts import (
    artifact_receipt_present,
    get_recorded_receipt_artifact_types,
)
from ralph.mcp.artifacts.format_docs import load_bundled_example
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.prompt_prep import session_capabilities_for_agent_phase
from ralph.policy.loader import default_dir, load_policy
from ralph.policy.models import ArtifactsPolicy, PhaseDefinition, PolicyBundle
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.workspace.memory import MemoryWorkspace
from tests._artifact_format_docs_mock_session import MockSession
from tests._artifact_format_docs_mock_workspace import MockWorkspace
from tests._support.typed_accessors import must_text

_PROMPT_ARTIFACT_CALL = re.compile(
    r'`artifact_type` set to `"(?P<t1>[a-z_]+)"`|`artifact_type="(?P<t2>[a-z_]+)"`'
)
_SKILL_POINTER = re.compile(r"`(submit-[a-z-]+-artifact|submit-artifact)`")
_BACKTICK_NAME = re.compile(r"`([a-z_]+)`")

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "RALPH_TEST_CHANGE_REASONS test_every_default_phase_grades_the_type_its_prompt_and_skill_name": (
        "the invariant test spans template-regex inspection, phase prompt rendering, skill "
        "discovery, receipt verification, and policy artifact validation across all 8 pipeline "
        "phases, and its receipt leg now submits every phase's seeded document through the "
        "production ralph_submit_md_artifact handler and reads the receipt type back from the "
        "persisted receipt store, so a submit-to-receipt mapping defect can no longer be masked "
        "by a receipt written from the assertion's own input"
    ),
}


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


def _render_phase_prompt(
    *,
    bundle: PolicyBundle,
    phase_name: str,
    phase_def: PhaseDefinition,
    workspace_root: Path,
) -> str:
    workspace = MemoryWorkspace(root=str(workspace_root))
    workspace.write("PROMPT.md", "# Task prompt\n")
    workspace.write(".agent/PLAN.md", "# Plan\n")
    workspace.write(".agent/artifacts/plan.md", "# Plan\n")
    workspace.write(".agent/artifacts/development_result.md", "# Development Result\n")
    drain = phase_def.drain or phase_name
    caps = session_capabilities_for_agent_phase(
        drain,
        phase=phase_name,
        pipeline_policy=bundle.pipeline,
        agents_policy=bundle.agents,
    )
    ctx = PromptPhaseContext(
        phase=phase_name,
        workspace=workspace,
        pipeline_policy=bundle.pipeline,
        session_caps=caps,
        workspace_root=workspace_root,
    )
    opts = PromptPhaseOptions(artifacts_policy=bundle.artifacts)
    prompt_path = materialize_prompt_for_phase(ctx, opts)
    return workspace.read(prompt_path)


def _extract_prompt_submission_type(rendered_prompt: str, *, phase_name: str) -> str:
    calls: list[str] = []
    for match in _PROMPT_ARTIFACT_CALL.finditer(rendered_prompt):
        val = match.group("t1") or match.group("t2")
        if val is not None:
            calls.append(val)
    assert calls, f"Phase {phase_name} rendered prompt does not name any submission artifact type"
    assert len(set(calls)) == 1, (
        f"Phase {phase_name} names multiple conflicting artifact types: {set(calls)}"
    )
    return calls[0]


def _extract_skill_contract_type(
    rendered_prompt: str,
    *,
    phase_name: str,
    expected_type: str,
    umbrella_supported: set[str],
    skills_dir: Path,
) -> str:
    referenced_skills: list[str] = sorted(
        {match.group(1) for match in _SKILL_POINTER.finditer(rendered_prompt)}
    )
    if not referenced_skills:
        return expected_type
    assert len(referenced_skills) == 1, (
        f"Phase {phase_name} references multiple submit skills: {referenced_skills}"
    )
    skill_name = referenced_skills[0]
    if skill_name == "submit-artifact":
        assert expected_type in umbrella_supported, (
            f"Phase {phase_name} uses umbrella submit-artifact but {expected_type} "
            f"not in supported types: {umbrella_supported}"
        )
        return expected_type
    concrete_type = skill_name.removeprefix("submit-").removesuffix("-artifact").replace("-", "_")
    assert concrete_type == expected_type, (
        f"Phase {phase_name} skill pointer {skill_name} does not match {expected_type}"
    )
    skill_file = skills_dir / f"{skill_name}.md"
    assert skill_file.is_file(), f"Skill file {skill_name}.md does not exist"
    skill_content = skill_file.read_text(encoding="utf-8")
    assert concrete_type in skill_content, (
        f"Skill content in {skill_name}.md does not name {concrete_type}"
    )
    return concrete_type


@dataclass
class _ContractSession(MockSession):
    run_id: str = "run-artifact-contract"


_ARTIFACT_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "planning_analysis_decision": ("plan",),
}


def _seed_document(artifact_type: str) -> str:
    document = load_bundled_example(artifact_type)
    assert document is not None, (
        f"no bundled format-doc example is available for artifact type {artifact_type!r}"
    )
    return document


def _submit_through_handler(
    session: _ContractSession,
    workspace: MockWorkspace,
    *,
    artifact_type: str,
    phase_name: str,
) -> None:
    result = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": artifact_type, "content": _seed_document(artifact_type)},
    )
    assert result.is_error is False, (
        f"production submit handler rejected the seeded {artifact_type!r} document "
        f"for phase {phase_name!r}: {json.dumps(json.loads(must_text(result.content[0])))}"
    )


def _submit_through_handler_and_read_receipt_type(
    workspace_root: Path,
    *,
    phase_name: str,
    artifact_type: str,
    drain: str,
) -> str:
    workspace_path = workspace_root / f"submit_ws_{phase_name}"
    workspace_path.mkdir(parents=True, exist_ok=True)
    workspace = MockWorkspace(workspace_path)
    run_id = f"run-artifact-contract-{phase_name}"
    for prerequisite in _ARTIFACT_PREREQUISITES.get(artifact_type, ()):
        prerequisite_run_id = f"{run_id}-prerequisite-{prerequisite}"
        _submit_through_handler(
            _ContractSession(
                session_id=prerequisite_run_id,
                drain="planning",
                run_id=prerequisite_run_id,
            ),
            workspace,
            artifact_type=prerequisite,
            phase_name=phase_name,
        )
    _submit_through_handler(
        _ContractSession(session_id=run_id, drain=drain, run_id=run_id),
        workspace,
        artifact_type=artifact_type,
        phase_name=phase_name,
    )
    recorded = get_recorded_receipt_artifact_types(workspace_path, run_id)
    assert len(recorded) == 1, (
        f"production submit handler recorded receipt types {recorded} for run {run_id!r}; "
        "expected exactly one persisted receipt"
    )
    receipt_type = recorded[0]
    assert artifact_receipt_present(workspace_path, run_id, receipt_type) is True, (
        f"receipt {receipt_type!r} for run {run_id!r} is not readable back from the workspace"
    )
    return receipt_type


def _assert_artifact_contract(
    *,
    phase_name: str,
    prompt_type: str,
    skill_type: str,
    receipt_type: str,
    policy_type: str,
) -> None:
    assert prompt_type == skill_type == receipt_type == policy_type, (
        f"Artifact contract mismatch in phase {phase_name!r}: "
        f"prompt={prompt_type!r}, "
        f"skill={skill_type!r}, "
        f"receipt={receipt_type!r}, "
        f"policy={policy_type!r}"
    )


def test_every_default_phase_grades_the_type_its_prompt_and_skill_name(tmp_path: Path) -> None:
    bundle = load_policy(default_dir())
    skills_dir = Path(str(resources.files("ralph.skills") / "content"))
    umbrella_content = (skills_dir / "submit-artifact.md").read_text(encoding="utf-8")
    m_umbrella = re.search(
        r"Supported `artifact_type` values:(.*?)(?:\n\n|\.\s+The)",
        umbrella_content,
        re.DOTALL,
    )
    assert m_umbrella is not None
    umbrella_supported: set[str] = {
        m.group(1) for m in _BACKTICK_NAME.finditer(m_umbrella.group(1))
    }

    checked_phases: list[str] = []
    for phase_name, phase_def in bundle.pipeline.phases.items():
        required = resolve_phase_required_artifact(
            bundle.pipeline, bundle.artifacts, phase=phase_name
        )
        if required is None:
            continue

        rendered_prompt = _render_phase_prompt(
            bundle=bundle,
            phase_name=phase_name,
            phase_def=phase_def,
            workspace_root=tmp_path / f"render_{phase_name}",
        )

        prompt_type = _extract_prompt_submission_type(rendered_prompt, phase_name=phase_name)
        skill_type = _extract_skill_contract_type(
            rendered_prompt,
            phase_name=phase_name,
            expected_type=prompt_type,
            umbrella_supported=umbrella_supported,
            skills_dir=skills_dir,
        )
        receipt_type = _submit_through_handler_and_read_receipt_type(
            tmp_path,
            phase_name=phase_name,
            artifact_type=prompt_type,
            drain=phase_def.drain or phase_name,
        )
        policy_type = required.artifact_type

        _assert_artifact_contract(
            phase_name=phase_name,
            prompt_type=prompt_type,
            skill_type=skill_type,
            receipt_type=receipt_type,
            policy_type=policy_type,
        )
        checked_phases.append(phase_name)

    expected_phases = {
        "planning",
        "planning_analysis",
        "development",
        "development_commit_review",
        "development_commit",
        "development_analysis",
        "development_final_commit_review",
        "development_final_commit",
    }
    assert set(checked_phases) == expected_phases


def test_contract_invariant_detects_prompt_mismatch() -> None:
    with pytest.raises(AssertionError, match="Artifact contract mismatch"):
        _assert_artifact_contract(
            phase_name="development_commit_review",
            prompt_type="stale_review",
            skill_type="commit_review",
            receipt_type="commit_review",
            policy_type="commit_review",
        )


def test_contract_invariant_detects_skill_mismatch() -> None:
    with pytest.raises(AssertionError, match="Artifact contract mismatch"):
        _assert_artifact_contract(
            phase_name="development_commit_review",
            prompt_type="commit_review",
            skill_type="stale_review",
            receipt_type="commit_review",
            policy_type="commit_review",
        )


def test_contract_invariant_detects_receipt_mismatch() -> None:
    with pytest.raises(AssertionError, match="Artifact contract mismatch"):
        _assert_artifact_contract(
            phase_name="development_commit_review",
            prompt_type="commit_review",
            skill_type="commit_review",
            receipt_type="stale_review",
            policy_type="commit_review",
        )


def test_contract_invariant_detects_unsupported_umbrella_type() -> None:
    skills_dir = Path(str(resources.files("ralph.skills") / "content"))
    rendered = "Optional aid: use `submit-artifact` when available"
    with pytest.raises(AssertionError, match="not in supported types"):
        _extract_skill_contract_type(
            rendered,
            phase_name="mock_phase",
            expected_type="unknown_nonexistent_artifact",
            umbrella_supported={"plan", "development_result"},
            skills_dir=skills_dir,
        )
