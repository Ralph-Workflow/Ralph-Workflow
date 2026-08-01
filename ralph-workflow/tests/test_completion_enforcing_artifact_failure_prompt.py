"""Regression coverage for completion-enforcing agents missing artifacts."""

from __future__ import annotations

import types
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.config.enums import AgentTransport
from ralph.config.general_config import GeneralConfig
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import (
    resolve_phase_required_artifact,
    retry_hint_path,
)
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._session_fake_mcp_bridge import _FakeMcpBridge
from tests._session_registry_factory import _RegistryFactory

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    return load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")


def _completion_failure_invoke(
    transport: AgentTransport,
    workspace_root: Path,
    *,
    write_artifact: bool = False,
) -> tuple[object, list[int]]:
    invoke_count = [0]

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> Iterator[str]:
        del config, prompt_file, options
        invoke_count[0] += 1
        if write_artifact:
            artifact_path = workspace_root / ".agent" / "artifacts" / "development_result.md"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text("---\ntype: development_result\nstatus: completed\n---\n", encoding="utf-8")

        def generate() -> Iterator[str]:
            yield "agent output"
            check_process_result(
                types.SimpleNamespace(returncode=0),
                transport.value,
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy_for_transport(transport),
                    workspace_path=workspace_root,
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                    completion_run_id=transport.value,
                ),
            )

        return generate()

    return fake_invoke_agent, invoke_count


@pytest.mark.parametrize(
    ("agent_name", "transport"),
    [("agy", AgentTransport.AGY), ("claude", AgentTransport.CLAUDE)],
)
def test_completion_enforcing_agent_regression_missing_receipt_writes_canonical_hint(
    tmp_path: Path,
    agent_name: str,
    transport: AgentTransport,
) -> None:
    """S-1: a clean exit without a receipt leaves the standard resubmit prompt."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")
    _RegistryFactory._agent_config = AgentConfig(cmd=agent_name, transport=transport)
    invoke, invoke_count = _completion_failure_invoke(transport, tmp_path)
    display_context = make_display_context()
    deps = make_test_pipeline_deps(
        display_context=display_context,
        bridge=_FakeMcpBridge(),
        registry_factory=_RegistryFactory.from_config,
        artifact_requirements_resolver=resolve_phase_required_artifact,
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(agent_name=agent_name, phase="development", prompt_file=str(prompt_file)),
        UnifiedConfig(general=GeneralConfig(max_retries=0)),
        deps,
        WorkspaceScope(tmp_path),
        display_context=display_context,
        invoke_agent=invoke,
        agent_invocation_error=AgentInvocationError,
        policy_bundle=_default_policy_bundle(),
    )

    hint = tmp_path / retry_hint_path("development")
    assert result == PipelineEvent.AGENT_FAILURE
    assert invoke_count == [1]
    assert hint.exists()
    assert "development_result" in hint.read_text(encoding="utf-8")
    assert ".agent/artifacts/development_result.md" in hint.read_text(encoding="utf-8")
    assert "ralph_submit_md_artifact" in hint.read_text(encoding="utf-8")


def test_completion_enforcing_agent_regression_missing_sentinel_does_not_resubmit_artifact(
    tmp_path: Path,
) -> None:
    """S-1: an existing artifact with no sentinel must not receive a resubmit hint."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")
    _RegistryFactory._agent_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    invoke, _ = _completion_failure_invoke(AgentTransport.AGY, tmp_path, write_artifact=True)
    display_context = make_display_context()
    deps = make_test_pipeline_deps(
        display_context=display_context,
        bridge=_FakeMcpBridge(),
        registry_factory=_RegistryFactory.from_config,
        artifact_requirements_resolver=resolve_phase_required_artifact,
    )

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(agent_name="agy", phase="development", prompt_file=str(prompt_file)),
        UnifiedConfig(general=GeneralConfig(max_retries=0)),
        deps,
        WorkspaceScope(tmp_path),
        display_context=display_context,
        invoke_agent=invoke,
        agent_invocation_error=AgentInvocationError,
        policy_bundle=_default_policy_bundle(),
    )

    assert result == PipelineEvent.AGENT_FAILURE
    assert (tmp_path / ".agent" / "artifacts" / "development_result.md").exists()
    assert not (tmp_path / retry_hint_path("development")).exists()
