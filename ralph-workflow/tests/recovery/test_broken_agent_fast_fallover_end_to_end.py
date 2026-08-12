"""End-to-end regression for broken-agent direct-MCP fallover."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, BaseExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import AgentInvocationError, CompletionCheckOptions, check_process_result
from ralph.agents.timeout_clock import FakeClock
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

    from ralph.agents.execution_state import LiveDescendantHandle
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent
    from ralph.process.liveness import LivenessProbe


class _CompletionGateStrategy(BaseExecutionStrategy):
    def supports_session_continuation(self) -> bool:
        return True

    def supports_completion_enforcement(self) -> bool:
        return True

    def classify_exit(
        self,
        handle: LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, completion_signals, liveness_probe
        return AgentExecutionState.RESUMABLE_CONTINUE


class _ExitedHandle:
    returncode = 0
    pid = None


def _no_completion_signals(*args: object, **kwargs: object) -> CompletionSignals:
    del args, kwargs
    return CompletionSignals(False, False, ())


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

    def broken_agent_invoke(*_args: object, **_kwargs: object) -> Iterator[str]:
        """S-6: trigger the real fast-exit completion gate, not a direct raise."""
        invocations.append("dev")
        yield from ()
        check_process_result(
            _ExitedHandle(),
            "dev",
            [],
            CompletionCheckOptions(
                execution_strategy=_CompletionGateStrategy(),
                workspace_path=Path("synthetic://broken-agent"),
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    parent_exit_grace_seconds=0.0,
                    descendant_wait_timeout_seconds=0.0,
                ),
                evaluate_completion_fn=_no_completion_signals,
                elapsed_seconds=2.0,
            ),
            _clock=FakeClock(),
        )

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
