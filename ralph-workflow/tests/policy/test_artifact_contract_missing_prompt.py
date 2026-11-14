from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline import effect_executor
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.policy.validation.artifact_contract import (
    ArtifactContractMismatchError,
    validate_artifact_contract,
)
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

PHASE = "development_commit_review"
GRADED_TYPE = "commit_review"
OTHER_TYPE = "commit_" + "cleanup"


def _required_artifact() -> RequiredArtifact:
    return RequiredArtifact(
        phase=PHASE,
        artifact_type=GRADED_TYPE,
        artifact_path=f".agent/artifacts/{GRADED_TYPE}.md",
        markdown_path=None,
        normalizer=None,
    )


def test_artifact_contract_regression_missing_rendered_prompt_is_not_a_type_mismatch(
    tmp_path: Path,
) -> None:
    try:
        validate_artifact_contract(
            phase=PHASE,
            prompt_file=tmp_path / "missing_prompt.md",
            workspace_root=tmp_path,
            required_artifact=_required_artifact(),
        )
    except ArtifactContractMismatchError as exc:
        pytest.fail(f"a missing rendered prompt must not be reported as a type mismatch: {exc}")


def test_artifact_contract_regression_missing_rendered_prompt_skips_the_skill_pointer_leg(
    tmp_path: Path,
) -> None:
    consulted: list[str] = []

    def skill_content_provider(name: str) -> str:
        consulted.append(name)
        return "submit-artifact skill body"

    validate_artifact_contract(
        phase=PHASE,
        prompt_file=tmp_path / "missing_prompt.md",
        workspace_root=tmp_path,
        required_artifact=_required_artifact(),
        skill_content_provider=skill_content_provider,
    )

    assert consulted == []


def test_artifact_contract_regression_rendered_prompt_naming_another_type_still_fails_loudly(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(f"## Submit a `{OTHER_TYPE}` artifact\n", encoding="utf-8")

    with pytest.raises(ArtifactContractMismatchError) as raised:
        validate_artifact_contract(
            phase=PHASE,
            prompt_file=prompt_file,
            workspace_root=tmp_path,
            required_artifact=_required_artifact(),
        )

    error = raised.value
    assert error.phase == PHASE
    assert error.prompted_type == OTHER_TYPE
    assert error.graded_type == GRADED_TYPE
    assert PHASE in str(error)
    assert OTHER_TYPE in str(error)
    assert GRADED_TYPE in str(error)


def test_artifact_contract_regression_missing_rendered_prompt_does_not_preempt_the_agent(
    tmp_path: Path,
) -> None:
    calls = 0

    def invoke(*_args: object, **_kwargs: object) -> list[str]:
        nonlocal calls
        calls += 1
        return ["done"]

    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class _Registry:
        def get(self, _name: str) -> AgentConfig:
            return agent_config

    class _Bridge:
        def shutdown(self) -> None:
            pass

    deps = make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=_Bridge(),
        master_prompt_materializer=lambda *_args, **_kwargs: str(tmp_path / "MASTER.md"),
        registry_factory=lambda _config: _Registry(),
    )
    effect = InvokeAgentEffect(
        agent_name="reviewer",
        phase=PHASE,
        prompt_file=str(tmp_path / "missing_prompt.md"),
        drain="commit",
    )

    result = effect_executor.execute_agent_effect(
        effect,
        UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0)),
        deps,
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        invoke_agent=invoke,
        agent_invocation_error=AgentInvocationError,
        required_artifact=_required_artifact(),
    )

    assert calls == 1
    assert result is not None
