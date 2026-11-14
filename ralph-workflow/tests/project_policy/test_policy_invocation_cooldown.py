from __future__ import annotations


import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.state import PipelineState
from ralph.project_policy import cli_integration, remediation
from ralph.project_policy.cli_integration import LoadResult
from ralph.project_policy.pipeline_graph import PHASE_REMEDIATION
from ralph.workspace.scope import WorkspaceScope
from tests.project_policy.policy_corpus import policy_invocation_bundle


def _load_result() -> LoadResult:
    return LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(root="/test/project", allowed_roots=["/test/project"]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=policy_invocation_bundle(),
        run_id="test-run-id",
    )


def test_real_policy_invoker_waits_for_owner_cooldown_across_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    invocations: list[str] = []

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> PipelineEvent:
        invocations.append(cast("str", getattr(effect, "agent_name", "")))
        return PipelineEvent.AGENT_SUCCESS if len(invocations) == 4 else PipelineEvent.AGENT_FAILURE

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(effect_executor_module, "execute_agent_effect", invoke_effect)
    load_result = _load_result()
    workspace_scope = load_result.workspace_scope
    assert workspace_scope is not None
    display_context = make_display_context()
    invoke = cli_integration.make_production_invoke_agent(
        load_result,
        PipelineDeps(display_context=display_context),
        workspace_scope,
        None,
        display_context,
    )

    for _round in range(3):
        assert invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md") is False
    assert invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md") is True

    assert invocations == ["test-agent"] * 4
    assert clock.monotonic() == 60.0


def test_real_policy_invoker_routes_caught_launch_failure_through_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    invocations = 0

    def invoke_effect(*_args: object, **_kwargs: object) -> PipelineEvent:
        nonlocal invocations
        invocations += 1
        if invocations <= 3:
            raise AgentInvocationError("test-agent", 1, "binary failed to start")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(effect_executor_module, "execute_agent_effect", invoke_effect)
    load_result = _load_result()
    workspace_scope = load_result.workspace_scope
    assert workspace_scope is not None
    display_context = make_display_context()
    invoke = cli_integration.make_production_invoke_agent(
        load_result,
        PipelineDeps(display_context=display_context),
        workspace_scope,
        None,
        display_context,
    )

    for _round in range(3):
        with pytest.raises(remediation.RemediationInvocationError, match="binary failed to start"):
            invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md")
    assert invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md") is True

    assert invocations == 4
    assert clock.monotonic() == 60.0
