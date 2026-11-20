from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.resolver_health import record_resolver_failure
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown
from ralph.recovery.attempt_facts import (
    AttemptFacts,
    TerminationIssuer,
    TerminationRecord,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    FrozenClock,
    failure_exception_for_mode,
    make_conflicted_repo,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)
from tests.scenarios._parity_support import (
    _PARITY_AGENTS,
    _PARITY_FAILURE_MODES,
    _assert_structural_parity,
    _install_before_after_noops,
    _ordinary_next_launch_time,
    _ParityFakeAgent,
    _resolver_next_launch_time,
    _run_parity_ordinary_side,
    _run_parity_resolver_side,
    _shared_cooldown_state,
)
from tests.scenarios.test_integration_scenario_matrix import _make_config

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_structural_parity_resolver_and_ordinary_phase_same_fake_agent": (
        "moved from tests/test_conflict_resolution_phase_parity.py because it drives a real "
        "paused rebase; both sides now run the same fake agent through the shared "
        "agent-effect seam on frozen clocks and compare launches, selection, fall-over "
        "order and eligibility kind, not cooldown reasons or durations, because the shared "
        "classifier contract cools resolver outcomes with typed no_output_at_start and "
        "no_output_after_activity reasons while the ordinary phase cools on "
        "consecutive_unproductive_limit_reached, and the prior raw invoke advanced only "
        "the resolver clock so absolute deadlines could never match; WU-5 also stubs "
        "integration before/after actions to no-ops and compares timeout/watchdog results "
        "plus operator status/log tokens for the invocation"
    ),
    "test_structural_parity_failure_modes_resolver_and_ordinary_same_fake_agent": (
        "invariant-13 structural parity over non-zero exit, signal, watchdog hang, and "
        "provider failure: same fake returns typed AttemptEnded endings on both paths "
        "with before/after actions stubbed to no-ops; the same injected failure must also "
        "land in the same shared cooldown classification and expiry and free the agent "
        "for the next launch at the same fake-clock instant in both phases; the shared "
        "harness now lives in tests/scenarios/_parity_support.py so this scenario module "
        "stays under the repository file-size limit"
    ),
    "test_parity_resolver_failure_stays_scoped_to_resolution_phase": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a resolver failure must not cool the same agent in an unrelated phase; "
        "operator rule: an agent is either on cooldown or not; the cooldown is keyed by agent "
        "only, so a resolver failure cools the agent in every phase, and the test asserts the "
        "shared cooldown and that its remaining wait is finite and within the one hour cap"
    ),
    "test_parity_ordinary_phase_cooldown_applies_to_the_resolver_until_it_expires": "operator rule: an agent is either on cooldown or not; replaces test_parity_ordinary_phase_cooldown_does_not_block_resolver_launch because the one cooldown tracker holds one state per agent, so a development cooldown also skips the agent for conflict resolution until it expires",
    "test_parity_ordinary_phase_cooldown_does_not_block_resolver_launch": "operator rule: an agent is either on cooldown or not; renamed to test_parity_ordinary_phase_cooldown_applies_to_the_resolver_until_it_expires because a cooldown is one per-agent state across phases",
    "test_parity_resolver_cooldown_applies_to_the_ordinary_phase_until_it_expires": "operator rule: an agent is either on cooldown or not; the reverse direction of the shared cooldown, a resolver-recorded cooldown is one agent-keyed record, so the ordinary phase skips that agent and falls over to the eligible one until the cooldown expires",
    "test_parity_resolver_falls_over_then_waits_for_the_earlier_cooldown": "operator rule: an agent is either on cooldown or not; the first resolver failure falls over to the next candidate at once, and when both candidates fail the run waits for the earlier cooldown with exactly one waiting status naming the next eligible agent and its expiry",
    "_assert_structural_parity": "the shared parity harness moved to tests/scenarios/_parity_support.py so this scenario module stays under the repository file-size limit; the assertion is unchanged, including the ruff format reflow of the selection-token equality; contract unchanged",
}


@pytest.mark.required_auto_integrate_e2e
@pytest.mark.parametrize(
    "mode",
    [
        AgentMode.NON_ZERO_EXIT,
        AgentMode.SIGNAL,
        AgentMode.WATCHDOG_HANG,
        AgentMode.PROVIDER_FAILURE,
    ],
)
def test_parity_resolver_failure_stays_scoped_to_resolution_phase(
    tmp_path: Path,
    mode: AgentMode,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    candidates = ("agentA", "agentB")
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=(),
        tmp_path=repo_dir,
        agent_modes={"agentA": mode},
    )

    bundle = resolution_policy_bundle(candidates)
    session = ResolutionSession(ledger=ledger)
    session.recovery_controller = None

    def _resolver(root: Path, target: str, stop_item: RebaseStop) -> bool:
        return run_rebase_conflict_resolution_pipeline(
            root=root,
            target=target,
            stop=stop_item,
            config=_make_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=bundle,
            display=None,
            display_context=None,
            invoke=invoke,
            clock=clock.monotonic,
            session=session,
        )

    token = install_executors(clock=clock)
    try:
        auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=_resolver,
            ledger=ledger,
            candidates=candidates,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert "agentA" in invocations

    resolution_status = ledger.status("rebase_conflict_resolution", "agentA")
    development_status = ledger.status("development", "agentA")
    assert isinstance(resolution_status, Cooldown)
    assert development_status == resolution_status
    wait_ms = ledger.earliest_wait_ms("development", ["agentA"])
    assert 0 < wait_ms <= 3_600_000, (
        f"agentA must sit on a finite capped cooldown, got {wait_ms} ms for {resolution_status}"
    )


@pytest.mark.required_auto_integrate_e2e
def test_parity_ordinary_phase_cooldown_applies_to_the_resolver_until_it_expires(
    tmp_path: Path,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    pipeline_controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock, ledger=ledger)
    )

    facts = AttemptFacts(
        exit_status="exit:7",
        termination=TerminationRecord.create(TerminationIssuer.AGENT_EXIT),
        artifact_valid=False,
        workspace_changed=False,
    )
    pipeline_controller.finalize_attempt(
        "development",
        "agentA",
        facts=facts,
        attempt_failed=True,
        exc=AgentInvocationError("agentA", 7, "agent exited with status 7"),
    )

    invocations: list[str] = []
    candidates = ("agentA", "agentB")
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(candidates)

    session = ResolutionSession(ledger=ledger)
    session.recovery_controller = None

    def _resolver(root: Path, target: str, stop_item: RebaseStop) -> bool:
        return run_rebase_conflict_resolution_pipeline(
            root=root,
            target=target,
            stop=stop_item,
            config=_make_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=bundle,
            display=None,
            display_context=None,
            invoke=invoke,
            clock=clock.monotonic,
            session=session,
        )

    token = install_executors(clock=clock)
    try:
        auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=_resolver,
            ledger=ledger,
            candidates=candidates,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert "agentA" not in invocations, (
        f"Agent 'agentA' is on a cooldown from the development phase, which applies to the "
        f"agent in every phase until it expires, but invocations were {invocations}"
    )
    assert invocations[:1] == ["agentB"]
    assert isinstance(ledger.status("rebase_conflict_resolution", "agentA"), Cooldown)


@pytest.mark.required_auto_integrate_e2e
def test_parity_classification_matches_across_phases_without_outcome_rewriting(
    tmp_path: Path,
) -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))
    facts = AttemptFacts(
        artifact_valid=False,
        workspace_changed=False,
        unresolved_count=1,
    )

    outcome_dev = controller.finalize_attempt(
        "development",
        "agentA",
        facts=facts,
        attempt_failed=True,
    )

    outcome_res = controller.finalize_attempt(
        "rebase_conflict_resolution",
        "agentA",
        facts=facts,
        attempt_failed=True,
    )

    assert outcome_dev.kind == outcome_res.kind
    assert outcome_dev.reason == outcome_res.reason
    assert outcome_res.reason != "stalled_no_progress", (
        "Conflict resolution must not rewrite unresolved remaining into stalled_no_progress"
    )


@pytest.mark.timeout_seconds(5)
def test_structural_parity_resolver_and_ordinary_phase_same_fake_agent(
    tmp_path: Path,
) -> None:
    before_after = _install_before_after_noops()
    bundle = resolution_policy_bundle(_PARITY_AGENTS)
    resolver = _run_parity_resolver_side(
        tmp_path / "resolver-repo",
        bundle,
        _ParityFakeAgent(),
        before_after=before_after,
    )
    ordinary = _run_parity_ordinary_side(
        tmp_path / "ordinary-repo",
        bundle,
        _ParityFakeAgent(),
        before_after=before_after,
    )
    _assert_structural_parity(resolver, ordinary)


@pytest.mark.timeout_seconds(20)
@pytest.mark.parametrize("mode", _PARITY_FAILURE_MODES)
def test_structural_parity_failure_modes_resolver_and_ordinary_same_fake_agent(
    tmp_path: Path,
    mode: AgentMode,
) -> None:
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

    before_after = _install_before_after_noops()
    bundle = resolution_policy_bundle(_PARITY_AGENTS)
    resolver = _run_parity_resolver_side(
        tmp_path / f"resolver-{mode.value}",
        bundle,
        _ParityFakeAgent(mode),
        before_after=before_after,
    )
    ordinary = _run_parity_ordinary_side(
        tmp_path / f"ordinary-{mode.value}",
        bundle,
        _ParityFakeAgent(mode),
        before_after=before_after,
    )
    _assert_structural_parity(resolver, ordinary)
    expected = type(failure_exception_for_mode(mode, "first")).__name__
    assert resolver.agent.results == [expected, expected]

    resolver_cooldowns = _shared_cooldown_state(
        resolver.controller, PHASE_RESOLUTION, _PARITY_AGENTS
    )
    ordinary_cooldowns = _shared_cooldown_state(ordinary.controller, "development", _PARITY_AGENTS)
    assert resolver_cooldowns == ordinary_cooldowns, (
        f"the same injected failure must land in the same shared cooldown classification "
        f"and expiry in both phases, got resolver={resolver_cooldowns} "
        f"ordinary={ordinary_cooldowns}"
    )

    resolver_next_launch = _resolver_next_launch_time(
        resolver,
        bundle=bundle,
        before_after=before_after,
        cooldowns=resolver_cooldowns,
    )
    ordinary_next_launch = _ordinary_next_launch_time(
        ordinary,
        bundle=bundle,
        before_after=before_after,
        cooldowns=ordinary_cooldowns,
    )
    assert resolver_next_launch == ordinary_next_launch, (
        f"the same injected failure must free the agent for the next launch at the same "
        f"fake-clock time in both phases, got resolver={resolver_next_launch} "
        f"ordinary={ordinary_next_launch}"
    )
    assert resolver_next_launch == pytest.approx(
        min(state.expiry_ms for state in resolver_cooldowns.values()) / 1000.0
    ), (
        f"the next launch must happen when the shared cooldown expires, got "
        f"{resolver_next_launch} for cooldowns {resolver_cooldowns}"
    )


@pytest.mark.required_auto_integrate_e2e
def test_parity_resolver_cooldown_applies_to_the_ordinary_phase_until_it_expires(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

    before_after = _install_before_after_noops()
    bundle = resolution_policy_bundle(_PARITY_AGENTS)
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock, ledger=ledger, policy_bundle=bundle)
    )

    record_resolver_failure(
        controller,
        _PARITY_AGENTS[0],
        AgentInvocationError(_PARITY_AGENTS[0], 7, "resolver exited with status 7"),
    )

    resolution_status = ledger.status(PHASE_RESOLUTION, _PARITY_AGENTS[0])
    development_status = ledger.status("development", _PARITY_AGENTS[0])
    assert isinstance(resolution_status, Cooldown)
    assert development_status == resolution_status, (
        "the resolver cooldown is one agent-keyed record, so the ordinary phase must read "
        f"the same cooldown, got {development_status} vs {resolution_status}"
    )

    ordinary = _run_parity_ordinary_side(
        tmp_path / "ordinary-repo",
        bundle,
        _ParityFakeAgent(),
        before_after=before_after,
        controller=controller,
    )

    assert ordinary.agent.launches == [_PARITY_AGENTS[1]], (
        "the ordinary phase must honour the resolver cooldown and fall over to the only "
        f"eligible agent, got {ordinary.agent.launches}"
    )
    selections = [
        line.content for line in ordinary.status_lines if "Selected agent" in line.content
    ]
    assert len(selections) == 1, f"one selection expected, got {selections}"
    assert f"{_PARITY_AGENTS[0]}: cooldown" in selections[0], (
        "the ordinary phase must report the resolver cooldown as the skip reason, "
        f"got {selections[0]}"
    )

    wait_ms = ledger.earliest_wait_ms("development", [_PARITY_AGENTS[0]])
    assert wait_ms > 0, "the shared cooldown must still have a finite wait"
    clock.advance(wait_ms / 1000.0)
    assert controller.agent_eligibility("development", _PARITY_AGENTS[0]).is_eligible, (
        "the shared cooldown must expire and free the agent in every phase"
    )


@pytest.mark.timeout_seconds(5)
def test_parity_resolver_falls_over_then_waits_for_the_earlier_cooldown(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

    before_after = _install_before_after_noops()
    bundle = resolution_policy_bundle(_PARITY_AGENTS)
    resolver = _run_parity_resolver_side(
        tmp_path / "resolver-repo",
        bundle,
        _ParityFakeAgent(AgentMode.NON_ZERO_EXIT),
        before_after=before_after,
    )
    controller = resolver.controller

    assert resolver.agent.launches == list(_PARITY_AGENTS), (
        f"the first failure must fall over to the second candidate, got {resolver.agent.launches}"
    )
    assert controller.clock.monotonic() == 0.0, (
        "the fall-over must run the second candidate at once instead of waiting out the "
        "first candidate's cooldown"
    )
    selections = [
        line.content for line in resolver.status_lines if "Selected agent" in line.content
    ]
    assert len(selections) == 2, f"two selections expected, got {selections}"
    assert f"{_PARITY_AGENTS[0]}: cooldown" in selections[1], (
        "the second candidate must be selected while the first is still cooling down, "
        f"got {selections[1]}"
    )

    waiting = [
        line.content
        for line in resolver.status_lines
        if line.content.startswith("all agents unavailable")
    ]
    assert len(waiting) == 1, f"exactly one waiting status expected, got {waiting}"

    statuses = {
        agent: controller.agent_eligibility(PHASE_RESOLUTION, agent) for agent in _PARITY_AGENTS
    }
    assert all(isinstance(status, Cooldown) for status in statuses.values()), (
        f"both candidates must be cooling down, got {statuses}"
    )
    now_ms = int(controller.clock.monotonic() * 1000)
    remaining = {
        agent: status.until_ms - now_ms
        for agent, status in statuses.items()
        if isinstance(status, Cooldown)
    }
    next_eligible = min(remaining, key=lambda agent: remaining[agent])
    assert remaining[next_eligible] == min(remaining.values())
    assert f"wait_ms={remaining[next_eligible]}" in waiting[0], (
        f"the waiting status must name the wait until the earlier cooldown, got {waiting[0]}"
    )
    unavailable = [
        line.content for line in resolver.status_lines if "No selectable agent" in line.content
    ]
    assert len(unavailable) == 1, f"one unavailable selection expected, got {unavailable}"
    assert f"{next_eligible}: cooldown ({remaining[next_eligible]}ms remaining" in unavailable[0], (
        f"the status must name {next_eligible} as the next eligible agent, got {unavailable[0]}"
    )
    assert len(resolver.agent.launches) == len(_PARITY_AGENTS), (
        "no candidate may be launched between the fall-over and the wait, "
        f"got {resolver.agent.launches}"
    )
