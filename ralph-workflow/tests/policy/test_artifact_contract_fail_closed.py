from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline import effect_executor
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.policy.validation.artifact_contract import ArtifactContractMismatchError
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps


class _Bridge:
    def shutdown(self) -> None:
        pass


def _execute(tmp_path: Path, prompt_file: Path) -> tuple[int, ArtifactContractMismatchError]:
    calls = 0

    def invoke(*_args: object, **_kwargs: object) -> list[str]:
        nonlocal calls
        calls += 1
        return ["done"]

    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class _Registry:
        def get(self, _name: str) -> AgentConfig:
            return agent_config

    deps = make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=_Bridge(),
        master_prompt_materializer=lambda *_args, **_kwargs: str(tmp_path / "MASTER.md"),
        registry_factory=lambda _config: _Registry(),
    )
    effect = InvokeAgentEffect(
        agent_name="reviewer",
        phase="development_commit_review",
        prompt_file=str(prompt_file),
        drain="commit",
    )
    required = RequiredArtifact(
        phase="development_commit_review",
        artifact_type="commit_review",
        artifact_path=".agent/artifacts/commit_review.md",
        markdown_path=None,
        normalizer=None,
    )
    with pytest.raises(ArtifactContractMismatchError) as raised:
        effect_executor.execute_agent_effect(
            effect,
            UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0)),
            deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=invoke,
            agent_invocation_error=AgentInvocationError,
            required_artifact=required,
        )
    return calls, raised.value


def test_phase_entry_regression_missing_rendered_prompt_fails_before_invocation(
    tmp_path: Path,
) -> None:
    calls, error = _execute(tmp_path, tmp_path / "missing.md")

    assert calls == 0
    assert error.phase == "development_commit_review"
    assert error.prompted_type is None
    assert error.graded_type == "commit_review"


def test_phase_entry_regression_prompt_without_submission_type_fails_before_invocation(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Review the proposed change.", encoding="utf-8")

    calls, error = _execute(tmp_path, prompt_file)

    assert calls == 0
    assert error.phase == "development_commit_review"
    assert error.prompted_type is None
    assert error.graded_type == "commit_review"
