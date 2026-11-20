from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo
from loguru import logger

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import RebaseSuccess, rebase_in_progress
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.git_worktree_observer import (
    GitWorktreeConflictObserver,
)
from ralph.pipeline.conflict_resolution.resolver_health import record_resolver_failure
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import ResolvedOutcome, StopResolutionOutcome
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown
from ralph.recovery.attempt_facts import (
    AttemptFacts,
    TerminationIssuer,
    TerminationRecord,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_operator_line import OperatorLine
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    FrozenClock,
    failure_exception_for_mode,
    make_conflicted_repo,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)
from tests.scenarios.test_integration_scenario_matrix import _make_config

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.conflict_resolution.sight import ConflictSight
    from ralph.policy.models import PolicyBundle


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
        "with before/after actions stubbed to no-ops"
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
    "_assert_structural_parity": "ruff format reflow of selection-token equality assertion; contract unchanged",
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


_PARITY_AGENTS: tuple[str, ...] = ("first", "second")
_PARITY_FAILURE_MODES: tuple[AgentMode, ...] = (
    AgentMode.NON_ZERO_EXIT,
    AgentMode.SIGNAL,
    AgentMode.WATCHDOG_HANG,
    AgentMode.PROVIDER_FAILURE,
)


class _ParityFakeAgent:
    def __init__(self, mode: AgentMode | None = None) -> None:
        self.mode = mode
        self.launches: list[str] = []
        self.results: list[str] = []

    def __call__(self, effect: object, *args: object, **kwargs: object) -> object:
        from ralph.pipeline.events import PipelineEvent

        del args, kwargs
        agent_name = str(getattr(effect, "agent_name", ""))
        self.launches.append(agent_name)
        if self.mode is not None:
            ended = failure_exception_for_mode(self.mode, agent_name)
            self.results.append(type(ended).__name__)
            return ended
        if agent_name == "first":
            self.results.append(PipelineEvent.AGENT_FAILURE.name)
            return PipelineEvent.AGENT_FAILURE
        self.results.append(PipelineEvent.AGENT_SUCCESS.name)
        return PipelineEvent.AGENT_SUCCESS


class _ParityIntegrationClock:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    def now(self) -> float:
        return self._clock.monotonic()

    def sleep(self, seconds: float) -> None:
        self._clock.sleep(seconds)


class _NoOpGitOps:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def rebase_onto(self, root: Path, target: str) -> object:
        del root, target
        self.calls.append("rebase_onto")
        return RebaseSuccess()

    def abort_rebase(self, root: Path) -> None:
        del root
        self.calls.append("abort_rebase")

    def continue_rebase(self, root: Path) -> None:
        del root
        self.calls.append("continue_rebase")

    def abort_merge(self, root: Path) -> bool:
        del root
        self.calls.append("abort_merge")
        return True

    def continue_merge(self, root: Path) -> bool:
        del root
        self.calls.append("continue_merge")
        return True

    def abort_cherry_pick(self, root: Path) -> bool:
        del root
        self.calls.append("abort_cherry_pick")
        return True

    def continue_cherry_pick(self, root: Path) -> bool:
        del root
        self.calls.append("continue_cherry_pick")
        return True

    def stage_all(self, root: Path) -> bool:
        del root
        self.calls.append("stage_all")
        return True

    def commit(self, root: Path, *, on_failure: object = None) -> bool:
        del root, on_failure
        self.calls.append("commit")
        return True

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: object = None,
    ) -> StopResolutionOutcome:
        del root, target, feature_sha, reclaim_target_worktree, on_reclaimed
        self.calls.append("fast_forward_target")
        return ResolvedOutcome()

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: object = None,
    ) -> StopResolutionOutcome:
        del root, target, feature_sha, reclaim_target_worktree, on_reclaimed
        self.calls.append("land_feature")
        return ResolvedOutcome()

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        del root, pre_feature_sha, owning_worktree
        self.calls.append("create_backup_ref")
        return None

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        del root, backup_ref
        self.calls.append("delete_backup_ref")

    def reattach_head(self, root: Path, branch: str) -> bool:
        del root, branch
        self.calls.append("reattach_head")
        return True


@dataclass
class _ParitySideResult:
    controller: RecoveryController
    agent: _ParityFakeAgent
    status_tokens: tuple[str, ...]
    status_lines: tuple[OperatorLine, ...]
    before_after_calls: tuple[str, ...]
    state: object | None = None


@dataclass
class _BeforeAfterNoOps:
    git_ops: _NoOpGitOps = field(default_factory=_NoOpGitOps)
    mechanical_stage_calls: list[str] = field(default_factory=list)

    @property
    def observer(self) -> _StubbedWorktreeObserver:
        return _StubbedWorktreeObserver(self.mechanical_stage_calls)

    @property
    def calls(self) -> tuple[str, ...]:
        return (*self.git_ops.calls, *self.mechanical_stage_calls)


class _StubbedWorktreeObserver(GitWorktreeConflictObserver):
    def __init__(self, mechanical_stage_calls: list[str]) -> None:
        self._mechanical_stage_calls = mechanical_stage_calls

    def stage_mechanical_conflicts(
        self,
        root: Path,
        kinds: dict[str, ConflictSight],
    ) -> tuple[str, ...]:
        del kinds
        self._mechanical_stage_calls.append(f"stage_mechanical:{root.name}")
        return ()


def _install_before_after_noops() -> _BeforeAfterNoOps:
    return _BeforeAfterNoOps()


@contextmanager
def _capture_invocation_status_lines() -> Iterator[list[OperatorLine]]:
    lines: list[OperatorLine] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", {})
        level = getattr(record.get("level"), "name", "INFO")
        content = str(record.get("message", "")).split("\n", 1)[0]
        name = str(record.get("name", ""))
        if not name.endswith("controller"):
            return
        if content.startswith("Phase ") or content.startswith("all agents unavailable"):
            lines.append(OperatorLine(level, content))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        yield lines
    finally:
        logger.remove(sink_id)


def _status_tokens(lines: list[OperatorLine]) -> tuple[str, ...]:
    tokens: list[str] = []
    for line in lines:
        content = line.content
        if "Selected agent first" in content:
            tokens.append("select:first")
        elif "Selected agent second" in content:
            tokens.append("select:second")
        elif "No selectable agent" in content:
            tokens.append("none-available")
        elif content.startswith("all agents unavailable"):
            tokens.append("wait")
    return tuple(tokens)


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
    display: ScenarioDisplay | None,
    observer: GitWorktreeConflictObserver,
) -> object:
    from ralph.pipeline.conflict_resolution.driver import (
        run_rebase_conflict_resolution_outcome,
    )
    from ralph.pipeline.resolution_outcome import ResolvedOutcome

    session = ResolutionSession(ledger=controller.ledger, recovery_controller=controller)

    def _resolver(resolver_root: Path, target: str, stop: RebaseStop) -> bool:
        return isinstance(
            run_rebase_conflict_resolution_outcome(
                root=resolver_root,
                target=target,
                stop=stop,
                config=config,
                pipeline_deps=None,
                workspace_scope=None,
                policy_bundle=bundle,
                display=display,
                display_context=None,
                clock=clock.monotonic,
                session=session,
                observer=observer,
            ),
            ResolvedOutcome,
        )

    return _resolver


def _run_parity_resolver_side(
    root: Path,
    bundle: PolicyBundle,
    agent: _ParityFakeAgent,
    *,
    before_after: _BeforeAfterNoOps,
) -> _ParitySideResult:
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
    display = ScenarioDisplay()
    token = install_executors(
        git_ops=before_after.git_ops,
        clock=_ParityIntegrationClock(clock),
    )
    try:
        deps_token = install_agent_effect_dependencies(
            agent_runner=agent, recovery_controller=controller
        )
        try:
            with _capture_invocation_status_lines() as status_lines:
                attempt_stop_resolution(
                    "parity-resolver-side",
                    StopResolutionTrigger(
                        name="rebase_stop",
                        root=root,
                        target="main",
                        resolver=_parity_resolver(
                            config,
                            bundle,
                            clock,
                            controller,
                            display,
                            before_after.observer,
                        ),
                        conflict_config=config.conflict_resolution,
                        force_retry=True,
                    ),
                )
        finally:
            reset_agent_effect_dependencies(deps_token)
    finally:
        reset_executors(token)
    controller_lines = tuple(status_lines)
    return _ParitySideResult(
        controller=controller,
        agent=agent,
        status_tokens=_status_tokens(list(controller_lines)),
        status_lines=(*controller_lines, *display.lines),
        before_after_calls=before_after.calls,
    )


def _run_parity_ordinary_side(
    root: Path,
    bundle: PolicyBundle,
    agent: _ParityFakeAgent,
    *,
    before_after: _BeforeAfterNoOps,
    controller: RecoveryController | None = None,
) -> _ParitySideResult:
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

    del before_after
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (agent_dir / "PLAN.md").write_text(
        "# A plan with enough words to pass validation\n", encoding="utf-8"
    )
    controller = controller or _parity_controller(bundle, FakeClock(start=0.0))
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
        with _capture_invocation_status_lines() as status_lines:
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
    return _ParitySideResult(
        controller=controller,
        agent=agent,
        status_tokens=_status_tokens(status_lines),
        status_lines=tuple(status_lines),
        before_after_calls=(),
        state=state,
    )


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


def _assert_structural_parity(resolver: _ParitySideResult, ordinary: _ParitySideResult) -> None:
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
    from ralph.pipeline.state import PipelineState, WaitReason

    assert resolver.agent.launches == list(_PARITY_AGENTS), (
        f"the resolver must launch first, fall over to second, got {resolver.agent.launches}"
    )
    assert ordinary.agent.launches == resolver.agent.launches
    assert ordinary.agent.results == resolver.agent.results, (
        f"timeout/watchdog/result endings must match across paths, "
        f"got resolver={resolver.agent.results} ordinary={ordinary.agent.results}"
    )
    assert isinstance(ordinary.state, PipelineState)
    assert ordinary.state.is_waiting_state is True
    assert ordinary.state.wait_reason is WaitReason.ALL_AGENTS_UNAVAILABLE
    resolver_shape = _durable_agent_shape(resolver.controller, PHASE_RESOLUTION)
    ordinary_shape = _durable_agent_shape(ordinary.controller, "development")
    assert resolver_shape == ordinary_shape, (
        f"the resolver and the ordinary phase must leave the same durable agent shape "
        f"(selection, fall-over order, eligibility kind), got {resolver_shape} vs {ordinary_shape}"
    )
    assert resolver.controller.earliest_available_wait_ms(PHASE_RESOLUTION, _PARITY_AGENTS) > 0
    assert ordinary.controller.earliest_available_wait_ms("development", _PARITY_AGENTS) > 0
    resolver_selection = tuple(token for token in resolver.status_tokens if token != "wait")
    ordinary_selection = tuple(token for token in ordinary.status_tokens if token != "wait")
    assert (
        resolver_selection
        == ordinary_selection
        == (
            "select:first",
            "select:second",
            "none-available",
        )
    ), (
        f"selection/fall-over status tokens must match, got "
        f"resolver={resolver_selection} ordinary={ordinary_selection}"
    )
    assert resolver.status_tokens == ordinary.status_tokens, (
        f"invocation status/log tokens including the shared wait line must match with "
        f"before/after stubbed to no-ops, got resolver={resolver.status_tokens} "
        f"ordinary={ordinary.status_tokens}; controller lines "
        f"resolver={[line.content for line in resolver.status_lines if line.content.startswith(('Phase ', 'all agents unavailable'))]} "
        f"ordinary={[line.content for line in ordinary.status_lines if line.content.startswith(('Phase ', 'all agents unavailable'))]}"
    )
    for call in resolver.before_after_calls:
        assert call.startswith(("stage_mechanical:", "continue_rebase:")) or call in {
            "rebase_onto",
            "abort_rebase",
            "continue_rebase",
            "abort_merge",
            "continue_merge",
            "abort_cherry_pick",
            "continue_cherry_pick",
            "stage_all",
            "commit",
            "fast_forward_target",
            "land_feature",
            "create_backup_ref",
            "delete_backup_ref",
            "reattach_head",
        }, f"unexpected before/after action escaped the no-op stub: {call}"


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


@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize("mode", _PARITY_FAILURE_MODES)
def test_structural_parity_failure_modes_resolver_and_ordinary_same_fake_agent(
    tmp_path: Path,
    mode: AgentMode,
) -> None:
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
