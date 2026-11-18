from __future__ import annotations

from collections.abc import Callable

import pytest
from loguru import logger

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.effects.invoke_agent_effect import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.state import PipelineState
from ralph.policy.models import AgentChainConfig, AgentDrainConfig
from ralph.project_policy import cli_integration, remediation
from ralph.project_policy.cli_integration import LoadResult
from ralph.project_policy.pipeline_graph import PHASE_ANALYSIS, PHASE_REMEDIATION
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.scope import WorkspaceScope
from tests.project_policy.policy_corpus import policy_invocation_bundle

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_real_policy_analysis_invoker_cools_failed_agent_and_selects_next_candidate": "operator rule: an agent is either on cooldown or not; the ledger unproductive limit now starts an expiring cooldown with reason unproductive_limit, so the expected cooldown_decision reason is unproductive_limit",
}

_PRIMARY = "primary-agent"
_NEXT = "next-agent"
_ANALYSIS_CHAIN = (_PRIMARY, _NEXT)


def _load_result() -> LoadResult:
    return LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(root="/test/project", allowed_roots=["/test/project"]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=policy_invocation_bundle(),
        run_id="test-run-id",
    )


def _analysis_load_result() -> LoadResult:
    bundle = policy_invocation_bundle()
    chain = AgentChainConfig(agents=list(_ANALYSIS_CHAIN))
    analysis_bundle = bundle.model_copy(
        update={
            "agents": bundle.agents.model_copy(
                update={
                    "agent_chains": {**bundle.agents.agent_chains, PHASE_ANALYSIS: chain},
                    "agent_drains": {
                        **bundle.agents.agent_drains,
                        PHASE_ANALYSIS: AgentDrainConfig(chain=PHASE_ANALYSIS),
                    },
                }
            )
        }
    )
    return LoadResult(
        config=UnifiedConfig(
            agents={name: AgentConfig(cmd=name) for name in _ANALYSIS_CHAIN},
        ),
        workspace_scope=WorkspaceScope(root="/test/project", allowed_roots=["/test/project"]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=analysis_bundle,
        run_id="test-analysis-run-id",
    )


def _capture_cooldown_lines(run: Callable[[], object]) -> list[str]:
    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING", format="{message}")
    try:
        run()
    finally:
        logger.remove(sink)
    return [line.strip() for line in lines if "cooldown_decision" in line]


def test_real_policy_invoker_waits_for_owner_cooldown_across_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    invocations: list[str] = []

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invocations.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS if len(invocations) == 4 else PipelineEvent.AGENT_FAILURE

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(cli_integration, "execute_agent_effect", invoke_effect)
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


def test_real_policy_analysis_invoker_cools_failed_agent_and_selects_next_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    invocations: list[str] = []

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invocations.append(effect.agent_name)
        return PipelineEvent.AGENT_FAILURE

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(cli_integration, "execute_agent_effect", invoke_effect)
    load_result = _analysis_load_result()
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

    def run_rounds() -> None:
        for _round in range(4):
            assert invoke(phase=PHASE_ANALYSIS, prompt_path="prompt.md") is False

    lines = _capture_cooldown_lines(run_rounds)
    duration_ms = int(AgentFailureLedger(clock=clock).base_cooldown_seconds * 1000)

    assert invocations == [_PRIMARY, _PRIMARY, _PRIMARY, _NEXT]
    assert clock.monotonic() == 0.0
    matching = [
        line
        for line in lines
        if f"phase={PHASE_ANALYSIS}" in line
        and f"agent={_PRIMARY}" in line
        and f"reason={UnavailabilityReason.UNPRODUCTIVE_LIMIT}" in line
        and f"duration_ms={duration_ms}" in line
        and f"expiry_ms={duration_ms}" in line
    ]
    assert len(matching) == 1


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
    monkeypatch.setattr(cli_integration, "execute_agent_effect", invoke_effect)
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
