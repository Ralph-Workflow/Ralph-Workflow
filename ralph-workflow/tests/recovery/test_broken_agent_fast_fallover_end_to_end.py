"""End-to-end regression for broken-agent direct-MCP fallover."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.agents.invoke import AgentInvocationError, BrokenAgentExitError
from ralph.config.models import CcsConfig
from ralph.display.context import make_display_context
from ralph.pipeline import apply_session_capture
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import _FakeBridge, make_test_pipeline_deps

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.pipeline.agent_retry_intent import AgentRetryIntent


def _registry_factory(return_value: object) -> object:
    class Registry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            instance = MagicMock()
            instance.get.return_value = return_value
            return instance

    return Registry


def _config() -> MagicMock:
    config = MagicMock()
    config.general.verbosity = 2
    config.agents = {}
    config.ccs = CcsConfig()
    config.ccs_aliases = {}
    return config


def test_broken_agent_regression_falls_over_without_direct_mcp_retries(
    monkeypatch: MonkeyPatch,
) -> None:
    """S-1: preserve the broken-agent fallover intent across the full executor seam."""
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
    pipeline_deps = make_test_pipeline_deps(
        make_display_context(),
        bridge=_FakeBridge(),
        master_prompt_materializer=lambda **_kwargs: "PROMPT.md",
        registry_factory=_registry_factory(MagicMock()).from_config,
    )
    invocations: list[str] = []

    def broken_agent_invoke(*_args: object, **_kwargs: object) -> object:
        invocations.append("dev")
        raise BrokenAgentExitError("dev", reason="no_output")

    state = PipelineState.model_validate({"phase": "development"})

    result = effect_executor_module.execute_agent_effect(
        effect,
        _config(),
        pipeline_deps,
        WorkspaceScope("/tmp/worktree"),
        state=state,
        display_context=make_display_context(),
        invoke_agent=broken_agent_invoke,
        agent_invocation_error=AgentInvocationError,
        max_recovery_attempts=10,
    )

    captured_intents: list[AgentRetryIntent] = []
    original_pop = effect_executor_module.pop_last_captured_retry_intent

    def capture_retry_intent() -> AgentRetryIntent:
        intent = original_pop()
        captured_intents.append(intent)
        return intent

    monkeypatch.setattr(effect_executor_module, "pop_last_captured_retry_intent", capture_retry_intent)
    captured_state = apply_session_capture(state)

    assert result == PipelineEvent.AGENT_FAILURE
    assert invocations == ["dev"]
    assert len(captured_intents) == 1
    assert captured_intents[0].skip_same_agent_retries is True
    assert captured_state.agent_retry_intent.skip_same_agent_retries is True
