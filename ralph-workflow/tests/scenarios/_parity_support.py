from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest
from git import Repo
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import RebaseSuccess, rebase_in_progress
from ralph.pipeline.conflict_resolution.git_worktree_observer import GitWorktreeConflictObserver
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import ResolvedOutcome, StopResolutionOutcome
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_operator_line import OperatorLine
from tests.pipeline._auto_integrate_agent_failure_fixtures import failure_exception_for_mode

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.pipeline.conflict_resolution import RebaseStop
    from ralph.pipeline.conflict_resolution.sight import ConflictSight
    from ralph.policy.models import PolicyBundle

pytestmark = pytest.mark.subprocess_e2e

_PARITY_AGENTS: tuple[str, ...] = ("first", "second")
_PARITY_FAILURE_MODES: tuple[AgentMode, ...] = (
    AgentMode.NON_ZERO_EXIT,
    AgentMode.SIGNAL,
    AgentMode.WATCHDOG_HANG,
    AgentMode.PROVIDER_FAILURE,
)


class _ParityFakeAgent:
    def __init__(self, mode: AgentMode | None = None, clock: FakeClock | None = None) -> None:
        self.mode = mode
        self.clock = clock
        self.launches: list[str] = []
        self.launch_times: list[float] = []
        self.results: list[str] = []

    def __call__(self, effect: object, *args: object, **kwargs: object) -> object:
        from ralph.pipeline.events import PipelineEvent

        del args, kwargs
        agent_name = str(getattr(effect, "agent_name", ""))
        self.launches.append(agent_name)
        if self.clock is not None:
            self.launch_times.append(self.clock.monotonic())
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
    root: Path | None = None
    clock: FakeClock | None = None


class _CooldownReasonAndExpiry(NamedTuple):
    reason: str
    expiry_ms: int


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


def _parity_clock_and_controller(
    bundle: PolicyBundle,
    clock: FakeClock | None,
    controller: RecoveryController | None,
) -> tuple[FakeClock, RecoveryController]:
    if controller is not None:
        controller_clock = controller.clock
        effective_clock = clock if clock is not None else controller_clock
        if not isinstance(effective_clock, FakeClock):
            raise AssertionError("the parity sides must run on a fake clock")
        return effective_clock, controller
    effective_clock = clock if clock is not None else FakeClock(start=0.0)
    return effective_clock, _parity_controller(bundle, effective_clock)


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
    controller: RecoveryController | None = None,
    clock: FakeClock | None = None,
    prepare_repo: bool = True,
) -> _ParitySideResult:
    from ralph.pipeline import attempt_stop_resolution
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    if prepare_repo:
        _parity_paused_rebase(root)
    effective_clock, effective_controller = _parity_clock_and_controller(bundle, clock, controller)
    agent.clock = agent.clock if agent.clock is not None else effective_clock
    config = UnifiedConfig.model_validate(
        {
            "general": {"auto_integrate_enabled": True, "auto_integrate_target": "main"},
            "conflict_resolution": {"max_rounds_per_stop": 1, "max_rebase_conflict_stops": 1},
        }
    )
    display = ScenarioDisplay()
    token = install_executors(
        git_ops=before_after.git_ops,
        clock=_ParityIntegrationClock(effective_clock),
    )
    try:
        deps_token = install_agent_effect_dependencies(
            agent_runner=agent, recovery_controller=effective_controller
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
                            effective_clock,
                            effective_controller,
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
        controller=effective_controller,
        agent=agent,
        status_tokens=_status_tokens(list(controller_lines)),
        status_lines=(*controller_lines, *display.lines),
        before_after_calls=before_after.calls,
        root=root,
        clock=effective_clock,
    )


def _run_parity_ordinary_side(
    root: Path,
    bundle: PolicyBundle,
    agent: _ParityFakeAgent,
    *,
    before_after: _BeforeAfterNoOps,
    controller: RecoveryController | None = None,
    clock: FakeClock | None = None,
    state: object | None = None,
    steps: int = len(_PARITY_AGENTS),
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
    agent_dir.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (agent_dir / "PLAN.md").write_text(
        "# A plan with enough words to pass validation\n", encoding="utf-8"
    )
    effective_clock, effective_controller = _parity_clock_and_controller(bundle, clock, controller)
    agent.clock = agent.clock if agent.clock is not None else effective_clock
    agent_config = UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in _PARITY_AGENTS})
    display_ctx = make_display_context()
    display = ParallelDisplay(workspace_root=root, display_context=display_ctx, is_quiet=True)
    registry = AgentRegistry.from_config(agent_config)
    effective_state: object = (
        state
        if state is not None
        else PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(
                    agents=list(_PARITY_AGENTS), current_index=0, retries=0
                )
            },
            last_connectivity_state="online",
        )
    )
    token = install_agent_effect_dependencies(
        agent_runner=agent, recovery_controller=effective_controller
    )
    try:
        with _capture_invocation_status_lines() as status_lines:
            for _ in range(steps):
                effective_state = run_pipeline_step(
                    state=effective_state,
                    policy_bundle=bundle,
                    workspace_scope=WorkspaceScope(root),
                    config=agent_config,
                    display=display,
                    display_context=display_ctx,
                    verbosity=Verbosity.QUIET,
                    registry=registry,
                    pipeline_subscriber=None,
                    recovery_controller=effective_controller,
                )
    finally:
        reset_agent_effect_dependencies(token)
    return _ParitySideResult(
        controller=effective_controller,
        agent=agent,
        status_tokens=_status_tokens(status_lines),
        status_lines=tuple(status_lines),
        before_after_calls=(),
        state=effective_state,
        root=root,
        clock=effective_clock,
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
    assert ordinary.agent.launch_times == resolver.agent.launch_times, (
        f"the fake clock must observe the same launch instants on both paths, "
        f"got resolver={resolver.agent.launch_times} ordinary={ordinary.agent.launch_times}"
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


def _shared_cooldown_state(
    controller: RecoveryController,
    phase: str,
    agents: tuple[str, ...],
) -> dict[str, _CooldownReasonAndExpiry]:
    view: dict[str, _CooldownReasonAndExpiry] = {}
    for agent in agents:
        status = controller.ledger.status(phase, agent)
        assert isinstance(status, Cooldown), (
            f"agent '{agent}' must sit on a shared cooldown in phase '{phase}', got {status!r}"
        )
        view[agent] = _CooldownReasonAndExpiry(status.reason, status.until_ms)
    return view


def _advance_to_cooldown_expiry(
    clock: FakeClock,
    cooldowns: dict[str, _CooldownReasonAndExpiry],
) -> None:
    now_ms = int(clock.monotonic() * 1000)
    expiry_ms = min(state.expiry_ms for state in cooldowns.values())
    assert expiry_ms > now_ms, (
        f"the shared cooldown must still be running before the next launch, "
        f"got expiry {expiry_ms} ms at now {now_ms} ms"
    )
    clock.sleep((expiry_ms - now_ms) / 1000.0)


def _resolver_next_launch_time(
    result: _ParitySideResult,
    *,
    bundle: PolicyBundle,
    before_after: _BeforeAfterNoOps,
    cooldowns: dict[str, _CooldownReasonAndExpiry],
) -> float:
    clock = result.clock
    root = result.root
    assert clock is not None and root is not None
    _advance_to_cooldown_expiry(clock, cooldowns)
    launches_before = len(result.agent.launch_times)
    _run_parity_resolver_side(
        root,
        bundle,
        result.agent,
        before_after=before_after,
        controller=result.controller,
        clock=clock,
        prepare_repo=False,
    )
    assert len(result.agent.launch_times) > launches_before, (
        "the resolver must launch the agent again once the shared cooldown expires, "
        f"got launches {result.agent.launches}"
    )
    return result.agent.launch_times[launches_before]


def _ordinary_next_launch_time(
    result: _ParitySideResult,
    *,
    bundle: PolicyBundle,
    before_after: _BeforeAfterNoOps,
    cooldowns: dict[str, _CooldownReasonAndExpiry],
) -> float:
    clock = result.clock
    root = result.root
    assert clock is not None and root is not None
    _advance_to_cooldown_expiry(clock, cooldowns)
    launches_before = len(result.agent.launch_times)
    _run_parity_ordinary_side(
        root,
        bundle,
        result.agent,
        before_after=before_after,
        controller=result.controller,
        clock=clock,
        state=result.state,
        steps=1,
    )
    assert len(result.agent.launch_times) > launches_before, (
        "the ordinary phase must launch the agent again once the shared cooldown expires, "
        f"got launches {result.agent.launches}"
    )
    return result.agent.launch_times[launches_before]
