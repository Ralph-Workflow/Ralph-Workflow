from __future__ import annotations

from collections.abc import Callable

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.effects.invoke_agent_effect import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.state import PipelineState
from ralph.policy.models import AgentChainConfig
from ralph.project_policy import cli_integration, validators
from ralph.project_policy.cli_integration import LoadResult
from ralph.project_policy.pipeline_driver import run_policy_pipeline
from ralph.project_policy.pipeline_graph import (
    DEFAULT_ANALYSIS_CAP,
    DEFAULT_MAX_REMEDIATION_ATTEMPTS,
    PHASE_REMEDIATION,
)
from ralph.recovery.agent_failure_ledger import DEFAULT_CONSECUTIVE_LIMIT
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests.project_policy.policy_corpus import policy_invocation_bundle, seed_complete_corpus, stack

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_remediation_agent_that_changes_nothing_cools_down_and_fails_over": (
        "a remediation round now fails over within the round to the next available chain "
        "agent, through run_chain_attempt, which now owns retry and cooldown failover for every caller like the regular pipeline, instead of spending a whole "
        "round on each attempt by an agent that produced nothing; the next agent is reached "
        "in the first round, the silent agent still cools down for unproductive_limit, and "
        "the run still ends not ready with the same report"
    ),
}

_SILENT = "silent-agent"
_NEXT = "next-agent"


def _load_result() -> LoadResult:
    bundle = policy_invocation_bundle()
    chains = {
        **bundle.agents.agent_chains,
        PHASE_REMEDIATION: AgentChainConfig(agents=[_SILENT, _NEXT]),
    }
    return LoadResult(
        config=UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in (_SILENT, _NEXT)}),
        workspace_scope=WorkspaceScope(root="/test/project", allowed_roots=["/test/project"]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=bundle.model_copy(
            update={"agents": bundle.agents.model_copy(update={"agent_chains": chains})}
        ),
        run_id="test-no-progress-run-id",
    )


def _capture_warnings(run: Callable[[], object]) -> list[str]:
    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING", format="{message}")
    try:
        run()
    finally:
        logger.remove(sink)
    return [line.strip() for line in lines]


def test_remediation_agent_that_changes_nothing_cools_down_and_fails_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    invocations: list[str] = []

    def silent_clean_exit(effect: object, *_args: object, **_kwargs: object) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invocations.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(cli_integration, "execute_agent_effect", silent_clean_exit)
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
    workspace = MemoryWorkspace()
    findings = validators.validate_readiness(workspace, stack())
    assert findings
    emitted: list[str] = []
    results: list[object] = []

    def run() -> None:
        results.append(
            run_policy_pipeline(
                workspace,
                stack(),
                findings,
                invoke_agent=invoke,
                analysis_cap=DEFAULT_ANALYSIS_CAP,
                emit=emitted.append,
            )
        )

    warnings = _capture_warnings(run)

    assert invocations[:6] == [_SILENT, _SILENT, _SILENT, _NEXT, _NEXT, _NEXT]
    assert len(invocations) == DEFAULT_MAX_REMEDIATION_ATTEMPTS * 2 * DEFAULT_CONSECUTIVE_LIMIT
    cooldowns = [
        line
        for line in warnings
        if "cooldown_decision" in line
        and f"phase={PHASE_REMEDIATION}" in line
        and f"agent={_SILENT}" in line
        and f"reason={UnavailabilityReason.UNPRODUCTIVE_LIMIT}" in line
    ]
    assert cooldowns
    assert not any("agent reported success" in message for message in emitted)
    assert (
        sum("made no progress" in message for message in emitted)
        == DEFAULT_MAX_REMEDIATION_ATTEMPTS
    )
    (result,) = results
    assert not result.is_ready()
    assert result.report_lines[0] == (
        f"project-policy-readiness: remediation made no progress in "
        f"{DEFAULT_MAX_REMEDIATION_ATTEMPTS} attempts ({len(findings)} findings remain)"
    )


def test_remediation_attempt_that_resolves_a_finding_completes_on_the_same_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    invocations: list[str] = []

    def clean_exit(effect: object, *_args: object, **_kwargs: object) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invocations.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(cli_integration, "execute_agent_effect", clean_exit)
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

    results = [
        invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md", progress_check=lambda: True)
        for _ in range(DEFAULT_MAX_REMEDIATION_ATTEMPTS)
    ]

    assert results == [True] * DEFAULT_MAX_REMEDIATION_ATTEMPTS
    assert invocations == [_SILENT] * DEFAULT_MAX_REMEDIATION_ATTEMPTS


def test_a_round_fails_over_past_agents_that_produce_nothing_to_one_that_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    invocations: list[str] = []
    workspace = MemoryWorkspace()
    dead_a, dead_b, worker = "dead-a", "dead-b", "worker"

    def execute(effect: object, *_args: object, **_kwargs: object) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invocations.append(effect.agent_name)
            if effect.agent_name == worker:
                seed_complete_corpus(workspace)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(cli_integration, "execute_agent_effect", execute)
    bundle = policy_invocation_bundle()
    chains = {
        **bundle.agents.agent_chains,
        PHASE_REMEDIATION: AgentChainConfig(agents=[dead_a, dead_b, worker]),
    }
    load_result = LoadResult(
        config=UnifiedConfig(
            agents={name: AgentConfig(cmd=name) for name in (dead_a, dead_b, worker)}
        ),
        workspace_scope=WorkspaceScope(root="/test/project", allowed_roots=["/test/project"]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=bundle.model_copy(
            update={"agents": bundle.agents.model_copy(update={"agent_chains": chains})}
        ),
        run_id="test-failover-run-id",
    )
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
    findings = validators.validate_readiness(workspace, stack())

    result = run_policy_pipeline(
        workspace,
        stack(),
        findings,
        invoke_agent=lambda **kwargs: (
            invoke(**kwargs) if kwargs["phase"] == PHASE_REMEDIATION else True
        ),
        analysis_cap=DEFAULT_ANALYSIS_CAP,
    )

    assert worker in invocations
    assert not validators.validate_readiness(workspace, stack())
    assert result.findings == []
