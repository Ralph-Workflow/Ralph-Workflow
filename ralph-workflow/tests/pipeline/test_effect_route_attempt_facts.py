from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest

    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps

from ralph.agents.invoke import AgentInvocationError, AttemptEnded, InvokeOptions
from ralph.agents.system_clock import SystemClock
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps, effect_attempt_invoke
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._session_fake_mcp_bridge import _FakeMcpBridge
from tests._session_registry_factory import _RegistryFactory

_PHASE = "development"
_AGENT = "claude"


def _carrying_invoke(facts: AttemptFacts) -> object:
    def invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str]:
        del config, prompt_file, options
        yield "agent output line"
        return AttemptEnded(facts=facts)

    return invoke


def _config() -> UnifiedConfig:
    return UnifiedConfig(general=GeneralConfig(max_same_agent_retries=2))


def _pipeline_deps(tmp_path: Path, display_context: DisplayContext) -> PipelineDeps:
    _RegistryFactory._agent_config = AgentConfig(cmd=_AGENT, output_flag="--json-stream")
    return make_test_pipeline_deps(
        display_context,
        bridge=_FakeMcpBridge(),
        registry_factory=_RegistryFactory.from_config,
    )


def test_effect_route_returns_the_carried_attempt_ended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    display_context = make_display_context()
    deps = _pipeline_deps(tmp_path, display_context)
    carried_facts = AttemptFacts(
        event_kinds=("tool_use", "tool_result"),
        tool_calls=("read_file",),
    )
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")

    result = effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(agent_name=_AGENT, phase=_PHASE, prompt_file=str(prompt_file)),
        _config(),
        deps,
        WorkspaceScope(tmp_path),
        display_context=display_context,
        invoke_agent=_carrying_invoke(carried_facts),
        agent_invocation_error=AgentInvocationError,
    )

    assert isinstance(result, AttemptEnded)
    assert result.facts is carried_facts


def test_effect_route_carries_success_facts_into_the_attempt_result(tmp_path: Path) -> None:
    display_context = make_display_context()
    pipeline_deps = _pipeline_deps(tmp_path, display_context)
    carried_facts = AttemptFacts(
        event_kinds=("tool_use", "tool_result"),
        tool_calls=("read_file",),
    )
    attempt_deps = AttemptRunnerDeps(
        SystemClock(),
        effect_attempt_invoke(effect_executor_module.execute_agent_effect),
        lambda _phase, _agent: None,
        None,
        None,
    )
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        config=_config(),
        policy_bundle=None,
        deps=attempt_deps,
    )
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")

    result = run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        effect=InvokeAgentEffect(agent_name=_AGENT, phase=_PHASE, prompt_file=str(prompt_file)),
        config=_config(),
        controller=controller,
        deps=attempt_deps,
        pipeline_deps=pipeline_deps,
        workspace_scope=WorkspaceScope(tmp_path),
        display_context=display_context,
        invoke_agent=_carrying_invoke(carried_facts),
        agent_invocation_error=AgentInvocationError,
    )

    assert result.event == PipelineEvent.AGENT_SUCCESS
    assert result.facts.event_kinds == ("tool_use", "tool_result")
    assert result.facts.tool_calls == ("read_file",)
    assert result.facts.artifact_valid is True
