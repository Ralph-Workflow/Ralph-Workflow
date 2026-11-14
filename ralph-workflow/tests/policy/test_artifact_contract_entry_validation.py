from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.policy.validation.artifact_contract import (
    ArtifactContractMismatchError,
    extract_prompted_artifact_types,
    extract_referenced_skill_pointers,
    validate_artifact_contract,
)
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from ralph.pipeline.pipeline_deps import PipelineDeps

RETIRED_TYPE = "commit_" + "cleanup"
RETIRED_SKILL = f"submit-{RETIRED_TYPE}-artifact"


class _FakeBridge:
    def shutdown(self) -> None:
        pass


def _make_config() -> UnifiedConfig:
    return UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))


def _registry_factory(config: object) -> object:
    del config
    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class _Registry:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    return _Registry()


def _make_deps(tmp_path: Path) -> PipelineDeps:
    def fake_materialize_master_prompt(
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str:
        del workspace_root, name, default_product_criteria, worker_namespace
        return str(tmp_path / "MASTER_PROMPT.md")

    return make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=_FakeBridge(),
        master_prompt_materializer=fake_materialize_master_prompt,
        registry_factory=_registry_factory,
    )


def test_extract_prompted_artifact_types_from_rendered_prompt() -> None:
    rendered = (
        "## Submit a `commit_review` artifact\n\n"
        "1. **Read the format doc** at `.agent/artifact-formats/commit_review.md`\n"
        'arguments: `artifact_type` set to "commit_review" and content\n'
    )
    assert extract_prompted_artifact_types(rendered) == {"commit_review"}


def test_extract_prompted_artifact_types_from_template_call() -> None:
    template = "{{ render_artifact_submission('planning_analysis_decision', TOOL, example) }}"
    assert extract_prompted_artifact_types(template) == {"planning_analysis_decision"}


def test_extract_prompted_artifact_types_from_planning_submission() -> None:
    source = 'Submit the complete plan with tool (`artifact_type="plan"`).'
    assert extract_prompted_artifact_types(source) == {"plan"}


def test_extract_referenced_skill_pointers() -> None:
    rendered = "Optional aid: use `submit-commit-review-artifact` when available; format doc remains authoritative."
    assert extract_referenced_skill_pointers(rendered) == {"submit-commit-review-artifact"}

    umbrella = "use `submit-artifact` when available"
    assert extract_referenced_skill_pointers(umbrella) == {"submit-artifact"}


def test_validate_artifact_contract_matching_concrete_skill_passes() -> None:
    prompt = (
        "## Submit a `commit_review` artifact\n\n"
        "Optional aid: use `submit-commit-review-artifact` when available;\n"
    )
    req = RequiredArtifact(
        phase="development_commit_review",
        artifact_type="commit_review",
        artifact_path=".agent/artifacts/commit_review.md",
        markdown_path=None,
        normalizer=None,
    )
    validate_artifact_contract(
        phase="development_commit_review",
        prompt_text=prompt,
        required_artifact=req,
    )


def test_validate_artifact_contract_umbrella_skill_passes() -> None:
    prompt = (
        "## Submit a `development_analysis_decision` artifact\n\n"
        "Optional aid: use `submit-artifact` when available;\n"
    )
    req = RequiredArtifact(
        phase="development_analysis",
        artifact_type="development_analysis_decision",
        artifact_path=".agent/artifacts/development_analysis_decision.md",
        markdown_path=None,
        normalizer=None,
    )
    validate_artifact_contract(
        phase="development_analysis",
        prompt_text=prompt,
        required_artifact=req,
    )


def test_validate_artifact_contract_no_skill_pointer_passes() -> None:
    prompt = "## Submit a `planning_analysis_decision` artifact\n\nFollow instructions.\n"
    req = RequiredArtifact(
        phase="planning_analysis",
        artifact_type="planning_analysis_decision",
        artifact_path=".agent/artifacts/planning_analysis_decision.md",
        markdown_path=None,
        normalizer=None,
    )
    validate_artifact_contract(
        phase="planning_analysis",
        prompt_text=prompt,
        required_artifact=req,
    )


def test_validate_artifact_contract_none_required_artifact_is_noop() -> None:
    validate_artifact_contract(
        phase="arbitrary_phase",
        prompt_text="arbitrary prompt",
        required_artifact=None,
    )


def test_validate_artifact_contract_mismatch_prompted_type_raises() -> None:
    prompt = (
        f"## Submit a `{RETIRED_TYPE}` artifact\n\n"
        "Optional aid: use `submit-commit-review-artifact` when available;\n"
    )
    req = RequiredArtifact(
        phase="development_commit_review",
        artifact_type="commit_review",
        artifact_path=".agent/artifacts/commit_review.md",
        markdown_path=None,
        normalizer=None,
    )
    with pytest.raises(ArtifactContractMismatchError) as exc_info:
        validate_artifact_contract(
            phase="development_commit_review",
            prompt_text=prompt,
            required_artifact=req,
        )
    err = exc_info.value
    assert err.phase == "development_commit_review"
    assert err.prompted_type == RETIRED_TYPE
    assert err.graded_type == "commit_review"
    assert "development_commit_review" in str(err)
    assert RETIRED_TYPE in str(err)
    assert "commit_review" in str(err)


def test_validate_artifact_contract_concrete_skill_mismatch_raises() -> None:
    prompt = (
        "## Submit a `commit_review` artifact\n\n"
        f"Optional aid: use `{RETIRED_SKILL}` when available;\n"
    )
    req = RequiredArtifact(
        phase="development_commit_review",
        artifact_type="commit_review",
        artifact_path=".agent/artifacts/commit_review.md",
        markdown_path=None,
        normalizer=None,
    )
    with pytest.raises(ArtifactContractMismatchError) as exc_info:
        validate_artifact_contract(
            phase="development_commit_review",
            prompt_text=prompt,
            required_artifact=req,
        )
    assert RETIRED_SKILL in str(exc_info.value)


def test_validate_artifact_contract_umbrella_unsupported_type_raises() -> None:
    prompt = (
        "## Submit a `custom_unknown_artifact` artifact\n\n"
        "Optional aid: use `submit-artifact` when available;\n"
    )
    req = RequiredArtifact(
        phase="custom_phase",
        artifact_type="custom_unknown_artifact",
        artifact_path=".agent/artifacts/custom.md",
        markdown_path=None,
        normalizer=None,
    )
    with pytest.raises(ArtifactContractMismatchError) as exc_info:
        validate_artifact_contract(
            phase="custom_phase",
            prompt_text=prompt,
            required_artifact=req,
        )
    assert "submit-artifact" in str(exc_info.value)
    assert "custom_unknown_artifact" in str(exc_info.value)


def test_validate_artifact_contract_missing_submission_in_prompt_raises() -> None:
    prompt = "Just write some code and tests, do not submit any artifact.\n"
    req = RequiredArtifact(
        phase="development",
        artifact_type="development_result",
        artifact_path=".agent/artifacts/development_result.md",
        markdown_path=None,
        normalizer=None,
    )
    with pytest.raises(ArtifactContractMismatchError) as exc_info:
        validate_artifact_contract(
            phase="development",
            prompt_text=prompt,
            required_artifact=req,
        )
    assert exc_info.value.prompted_type is None
    assert exc_info.value.graded_type == "development_result"


def test_effect_executor_raises_contract_mismatch_before_agent_invocation(
    tmp_path: Path,
) -> None:
    config = _make_config()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(
        f"## Submit a `{RETIRED_TYPE}` artifact\n"
        f"Optional aid: use `{RETIRED_SKILL}` when available;\n",
        encoding="utf-8",
    )

    effect = InvokeAgentEffect(
        agent_name="reviewer",
        phase="development_commit_review",
        prompt_file=str(prompt_file),
        drain="commit",
    )
    req = RequiredArtifact(
        phase="development_commit_review",
        artifact_type="commit_review",
        artifact_path=".agent/artifacts/commit_review.md",
        markdown_path=None,
        normalizer=None,
    )

    invoked = False

    def invoker_spy(*_args: object, **_kwargs: object) -> list[str]:
        nonlocal invoked
        invoked = True
        return ["done"]

    deps = _make_deps(tmp_path)

    with pytest.raises(ArtifactContractMismatchError) as exc_info:
        effect_executor_module.execute_agent_effect(
            effect,
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=invoker_spy,
            agent_invocation_error=AgentInvocationError,
            required_artifact=req,
        )

    assert not invoked, "Agent must not be invoked when pre-entry contract validation fails"
    err = exc_info.value
    assert err.phase == "development_commit_review"
    assert err.prompted_type == RETIRED_TYPE
    assert err.graded_type == "commit_review"


def test_effect_executor_passes_when_artifact_contract_matches(
    tmp_path: Path,
) -> None:
    config = _make_config()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(
        "## Submit a `commit_review` artifact\n"
        "Optional aid: use `submit-commit-review-artifact` when available;\n",
        encoding="utf-8",
    )

    effect = InvokeAgentEffect(
        agent_name="reviewer",
        phase="development_commit_review",
        prompt_file=str(prompt_file),
        drain="commit",
    )
    req = RequiredArtifact(
        phase="development_commit_review",
        artifact_type="commit_review",
        artifact_path=".agent/artifacts/commit_review.md",
        markdown_path=None,
        normalizer=None,
    )

    invoked = False

    def invoker_spy(*_args: object, **_kwargs: object) -> list[str]:
        nonlocal invoked
        invoked = True
        return ["done"]

    deps = _make_deps(tmp_path)

    result = effect_executor_module.execute_agent_effect(
        effect,
        config,
        deps,
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        invoke_agent=invoker_spy,
        agent_invocation_error=AgentInvocationError,
        required_artifact=req,
    )

    assert invoked, "Agent must be invoked when pre-entry contract validation passes"
    assert result is not None
