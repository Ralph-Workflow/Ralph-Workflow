from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    SupervisionInfrastructureError,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
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
    make_conflicted_repo,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)
from tests.scenarios.test_integration_scenario_matrix import _make_config

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_structural_parity_resolver_and_ordinary_phase_same_fake_agent": (
        "moved from tests/test_conflict_resolution_phase_parity.py because it drives a real "
        "paused rebase; both sides now run the same fake agent through the shared "
        "agent-effect seam on frozen clocks and compare launches, selection, fall-over "
        "order and eligibility kind, not cooldown reasons or durations, because the shared "
        "classifier contract cools resolver outcomes with typed no_output_at_start and "
        "no_output_after_activity reasons while the ordinary phase cools on "
        "consecutive_unproductive_limit_reached, and the prior raw invoke advanced only "
        "the resolver clock so absolute deadlines could never match"
    ),
    "test_parity_resolver_failure_stays_scoped_to_resolution_phase": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a resolver failure must not cool the same agent in an unrelated phase"
    ),
    "test_parity_ordinary_phase_cooldown_does_not_block_resolver_launch": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a development-phase failure must not suppress the resolver chain"
    ),
}

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.policy.models import PolicyBundle


@pytest.mark.required_auto_integrate_e2e
@pytest.mark.parametrize(
    ("mode", "exc_factory"),
    [
        (
            AgentMode.NON_ZERO_EXIT,
            lambda agent: AgentInvocationError(agent, 7, f"agent '{agent}' exited with status 7"),
        ),
        (
            AgentMode.SIGNAL,
            lambda agent: AgentInvocationError(
                agent, -15, f"agent '{agent}' terminated by signal 15"
            ),
        ),
        (
            AgentMode.WATCHDOG_HANG,
            lambda agent: AgentInactivityTimeoutError(agent, 30.0),
        ),
        (
            AgentMode.PROVIDER_FAILURE,
            lambda agent: SupervisionInfrastructureError(
                agent, f"provider unavailable for agent '{agent}'"
            ),
        ),
    ],
)
def test_parity_resolver_failure_stays_scoped_to_resolution_phase(
    tmp_path: Path,
    mode: AgentMode,
    exc_factory: Callable[[str], BaseException],
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

    ordinary_eligibility = ledger.agent_eligibility("development", "agentA")
    assert ordinary_eligibility.is_eligible, (
        f"Agent 'agentA' failed only in conflict resolution and must remain eligible "
        f"in the unrelated development phase, got {ordinary_eligibility}"
    )


@pytest.mark.required_auto_integrate_e2e
def test_parity_ordinary_phase_cooldown_does_not_block_resolver_launch(
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

    assert "agentA" in invocations, (
        f"Agent 'agentA' was cooled down only in the development phase and must remain "
        f"eligible for conflict resolution, but invocations were {invocations}"
    )
    assert "agentB" not in invocations


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


_PARITY_AGENTS: tuple[str, ...] = ("first", "second")


class _ParityFakeAgent:
    def __init__(self) -> None:
        self.launches: list[str] = []

    def __call__(self, effect: object, *args: object, **kwargs: object) -> object:
        from ralph.pipeline.events import PipelineEvent

        del args, kwargs
        agent_name = str(getattr(effect, "agent_name", ""))
        self.launches.append(agent_name)
        if agent_name == "first":
            return PipelineEvent.AGENT_FAILURE
        return PipelineEvent.AGENT_SUCCESS


class _ParityIntegrationClock:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    def now(self) -> float:
        return self._clock.monotonic()

    def sleep(self, seconds: float) -> None:
        self._clock.sleep(seconds)


def _parity_controller(bundle: PolicyBundle, clock: FakeClock) -> RecoveryController:
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    return RecoveryController(
        options=RecoveryControllerOptions(clock=clock, ledger=ledger, policy_bundle=bundle)
    )


def _parity_paused_rebase(root: Path) -> None:
    from tests._scenario_git import commit_file, git

    root.mkdir(parents=True, exist_ok=True)
    Repo.init(root, initial_branch="main")
    commit_file(root, "shared.txt", "base\n", "base")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "shared.txt", "feature\n", "feature change")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("parity fixture did not pause in a conflicted rebase")


def _parity_resolver(
    config: UnifiedConfig,
    bundle: PolicyBundle,
    clock: FakeClock,
    controller: RecoveryController,
) -> object:
    from ralph.pipeline.resolution_outcome import ResolvedOutcome

    session = ResolutionSession(ledger=controller.ledger, recovery_controller=controller)

    def _resolver(resolver_root: Path, target: str, stop: RebaseStop) -> bool:
        return isinstance(
            run_rebase_conflict_resolution_pipeline(
                root=resolver_root,
                target=target,
                stop=stop,
                config=config,
                pipeline_deps=None,
                workspace_scope=None,
                policy_bundle=bundle,
                display=None,
                display_context=None,
                clock=clock.monotonic,
                session=session,
            ),
            ResolvedOutcome,
        )

    return _resolver


def _run_parity_resolver_side(
    root: Path, bundle: PolicyBundle, agent: _ParityFakeAgent
) -> RecoveryController:
    from ralph.pipeline import attempt_stop_resolution
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    _parity_paused_rebase(root)
    clock = FakeClock(start=0.0)
    controller = _parity_controller(bundle, clock)
    config = UnifiedConfig.model_validate(
        {
            "general": {"auto_integrate_enabled": True, "auto_integrate_target": "main"},
            "conflict_resolution": {"max_rounds_per_stop": 1, "max_rebase_conflict_stops": 1},
        }
    )
    token = install_executors(clock=_ParityIntegrationClock(clock))
    deps_token = install_agent_effect_dependencies(
        agent_runner=agent, recovery_controller=controller
    )
    try:
        attempt_stop_resolution(
            "parity-resolver-side",
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target="main",
                resolver=_parity_resolver(config, bundle, clock, controller),
                conflict_config=config.conflict_resolution,
                force_retry=True,
            ),
        )
    finally:
        reset_agent_effect_dependencies(deps_token)
        reset_executors(token)
    return controller


def _run_parity_ordinary_side(
    root: Path, bundle: PolicyBundle, agent: _ParityFakeAgent
) -> tuple[RecoveryController, object]:
    from ralph.agents.registry import AgentRegistry
    from ralph.config.enums import Verbosity
    from ralph.config.models import AgentConfig
    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )
    from ralph.pipeline.runner import run_pipeline_step
    from ralph.pipeline.state import AgentChainState, PipelineState

    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (agent_dir / "PLAN.md").write_text(
        "# A plan with enough words to pass validation\n", encoding="utf-8"
    )
    controller = _parity_controller(bundle, FakeClock(start=0.0))
    agent_config = UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in _PARITY_AGENTS})
    display_ctx = make_display_context()
    display = ParallelDisplay(workspace_root=root, display_context=display_ctx, is_quiet=True)
    registry = AgentRegistry.from_config(agent_config)
    state: object = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=list(_PARITY_AGENTS), current_index=0, retries=0)
        },
        last_connectivity_state="online",
    )
    token = install_agent_effect_dependencies(agent_runner=agent, recovery_controller=controller)
    try:
        for _ in _PARITY_AGENTS:
            state = run_pipeline_step(
                state=state,
                policy_bundle=bundle,
                workspace_scope=WorkspaceScope(root),
                config=agent_config,
                display=display,
                display_context=display_ctx,
                verbosity=Verbosity.QUIET,
                registry=registry,
                pipeline_subscriber=None,
                recovery_controller=controller,
            )
    finally:
        reset_agent_effect_dependencies(token)
    return controller, state


def _durable_agent_shape(controller: RecoveryController, phase: str) -> object:
    selection = controller.preferred_agent_index(phase, _PARITY_AGENTS)
    return (
        selection.index,
        selection.agent,
        tuple(name for name, _reason in selection.skipped_reasons),
        tuple(
            type(controller.agent_eligibility(phase, agent)).__name__ for agent in _PARITY_AGENTS
        ),
    )


@pytest.mark.timeout_seconds(5)
def test_structural_parity_resolver_and_ordinary_phase_same_fake_agent(tmp_path: Path) -> None:
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
    from ralph.pipeline.state import PipelineState, WaitReason

    bundle = resolution_policy_bundle(_PARITY_AGENTS)
    resolver_agent = _ParityFakeAgent()
    ordinary_agent = _ParityFakeAgent()
    resolver_controller = _run_parity_resolver_side(
        tmp_path / "resolver-repo", bundle, resolver_agent
    )
    ordinary_controller, ordinary_state = _run_parity_ordinary_side(
        tmp_path / "ordinary-repo", bundle, ordinary_agent
    )

    assert resolver_agent.launches == list(_PARITY_AGENTS), (
        f"the resolver must launch first, fall over to second, got {resolver_agent.launches}"
    )
    assert ordinary_agent.launches == resolver_agent.launches
    assert isinstance(ordinary_state, PipelineState)
    assert ordinary_state.is_waiting_state is True
    assert ordinary_state.wait_reason is WaitReason.ALL_AGENTS_UNAVAILABLE
    resolver_shape = _durable_agent_shape(resolver_controller, PHASE_RESOLUTION)
    ordinary_shape = _durable_agent_shape(ordinary_controller, "development")
    assert resolver_shape == ordinary_shape, (
        f"the resolver and the ordinary phase must leave the same durable agent shape "
        f"(selection, fall-over order, eligibility kind), got {resolver_shape} vs {ordinary_shape}"
    )
    assert resolver_controller.earliest_available_wait_ms(PHASE_RESOLUTION, _PARITY_AGENTS) > 0
    assert ordinary_controller.earliest_available_wait_ms("development", _PARITY_AGENTS) > 0
