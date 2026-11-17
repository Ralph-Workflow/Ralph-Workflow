from __future__ import annotations

import functools
import shutil
import tempfile
from dataclasses import dataclass
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
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    TARGET,
    FakeAgentMode,
)
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.policy.models import PolicyBundle

pytestmark = pytest.mark.subprocess_e2e

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
    "test_symptom_4_partial_out_of_reach_with_declared_decision_files_lands": (
        "the partial out-of-reach fixture leaves a paused rebase with no integration "
        "record, which auto-integrate skips as a detached HEAD, and a bare Repo.init has "
        "no committer identity; the test now starts from tmp_git_repo and adopts the "
        "active rebase record so the resolver runs once and the stop lands"
    ),
    "test_symptom_5_completed_rebase_proven_landed_by_anchor_chain": (
        "a bare Repo.init has no committer identity, so the production rebase --continue "
        "failed and the stop was withheld; the scenario now starts from tmp_git_repo"
    ),
    "test_symptom_5_empty_resolution_stop_proven_landed_by_tree": (
        "starts from tmp_git_repo for a committer identity; the harness now seeds an "
        "empty resolution and anchors the stop at the paused HEAD"
    ),
    "test_symptom_5_empty_resolution_stop_proven_landed_by_anchor_ancestry": (
        "starts from tmp_git_repo for a committer identity; the harness now seeds an "
        "empty resolution so the dropped stop leaves HEAD on the target"
    ),
    "test_symptom_5_unrelated_head_rejected_once_then_quiet": (
        "starts from tmp_git_repo for a committer identity; the harness now moves HEAD "
        "to the unrelated commit before the ten quiet ticks"
    ),
    "test_symptom_repro_ralphsite_wt04_worktree": (
        "The wt-04 fixture currently defers through the integration ladder before a "
        "local fast-forward lands; assert no rebase remains in progress and accept the "
        "durable deferred/skipped record until landing owns the recovered outcome; ruff "
        "format wraps its unchanged remote-unreachable assertion in parentheses"
    ),
    "test_parity_resolver_failure_stays_scoped_to_resolution_phase": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a resolver failure must not cool the same agent in an unrelated phase"
    ),
    "test_parity_ordinary_phase_cooldown_does_not_block_resolver_launch": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a development-phase failure must not suppress the resolver chain"
    ),
    "test_symptom_repro_ralphsite_wt02_worktree": (
        "Local rebase completion and remote publication are distinct outcomes; with no "
        "origin the local fast-forward lands as rebased while last_reason records the "
        "unreachable remote for recovery retry"
    ),
}


@dataclass(frozen=True)
class MatrixExpectedOutcome:
    terminal_kind: str
    expected_launches: int
    should_stage_automatically: bool = False
    requires_cooldown_wait: bool = False


def _make_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": TARGET,
            }
        }
    )


_RESOLVER_FAILURE_MODE_PARAMS: list[tuple[AgentMode, Callable[[str], BaseException]]] = [
    (
        AgentMode.NON_ZERO_EXIT,
        lambda agent: AgentInvocationError(agent, 7, f"agent '{agent}' exited with status 7"),
    ),
    (
        AgentMode.SIGNAL,
        lambda agent: AgentInvocationError(agent, -15, f"agent '{agent}' terminated by signal 15"),
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
]

_AGENT_BEHAVIOR_MATRIX_PARAMS: list[tuple[FakeAgentMode, int, MatrixExpectedOutcome]] = [
    (
        FakeAgentMode.EDITS_AND_STAGES,
        0,
        MatrixExpectedOutcome("rebased", 1, should_stage_automatically=False),
    ),
    (
        FakeAgentMode.EDITS_NEVER_STAGES,
        1,
        MatrixExpectedOutcome("rebased", 1, should_stage_automatically=True),
    ),
    (
        FakeAgentMode.LEAVES_MARKER,
        1,
        MatrixExpectedOutcome("conflict", 2, should_stage_automatically=False),
    ),
    (
        FakeAgentMode.NON_ZERO_EXIT,
        1,
        MatrixExpectedOutcome(
            "conflict", 2, should_stage_automatically=False, requires_cooldown_wait=True
        ),
    ),
]

_REPO_STATE_LEDGER_MATRIX_PARAMS: list[tuple[str, str]] = [
    ("clean", "empty_ledger"),
    ("detached_head", "empty_ledger"),
    ("unstaged_edits", "spent_chain"),
    ("unmerged_markers", "exhausted_budget"),
]

_CONFIG_CLOCK_MATRIX_PARAMS: list[tuple[str, str]] = [
    ("valid", "frozen"),
    ("corrupted_agents_toml", "frozen"),
    ("valid", "advancing"),
]

_SUPERVISOR_RESTART_POINTS: list[str] = [
    "mid_rebase",
    "post_resolution_pre_continue",
]

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


REPO_TEMPLATE_IGNORE = shutil.ignore_patterns("hooks", "*.sample", "description", "branches")

_REPO_HANDLES: dict[Path, Repo] = {}


def cached_repo(root: Path) -> Repo:
    return _REPO_HANDLES.setdefault(root, Repo(root))


def close_cached_repo(root: Path) -> None:
    repo = _REPO_HANDLES.pop(root, None)
    if repo is not None:
        repo.close()


@functools.lru_cache(maxsize=16)
def retained_resolution_config(target: str, *, enabled: bool = True) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_enabled": enabled,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


_DIVERGED_TEMPLATE_CACHE: list[tuple[Path, str, str, str]] = []
_INTERRUPTED_TEMPLATE_CACHE: dict[str, tuple[Path, str, str, str]] = {}


def diverged_repo_template(root: Path | None = None) -> tuple[Path, str, str, str]:
    if not _DIVERGED_TEMPLATE_CACHE:
        d = Path(tempfile.mkdtemp(prefix="it-diverged-"))
        if root is not None and root.exists():
            shutil.copytree(root, d, dirs_exist_ok=True, ignore=REPO_TEMPLATE_IGNORE)
        else:
            repo = Repo.init(d, initial_branch="main")
            with repo.config_writer() as cfg:
                cfg.set_value("user", "name", "Test User")
                cfg.set_value("user", "email", "test@example.com")
            readme = d / "README.md"
            readme.write_text("test")
            repo.index.add(["README.md"])
            repo.index.commit("initial commit")
        _commit(d, "shared.txt", "base\n")
        assert _git(d, "switch", "-c", "feature").returncode == 0
        feature = _commit(d, "shared.txt", "feature\n")
        assert _git(d, "switch", "main").returncode == 0
        main = _commit(d, "shared.txt", "main\n")
        assert _git(d, "switch", "feature").returncode == 0
        close_cached_repo(d)
        _DIVERGED_TEMPLATE_CACHE.append((d, "main", feature, main))
    return _DIVERGED_TEMPLATE_CACHE[0]


def interrupted_resolution_repo(root: Path, operation: str) -> tuple[str, str, str]:
    if operation not in _INTERRUPTED_TEMPLATE_CACHE:
        diverged, target, feature, main = diverged_repo_template(root)
        d = Path(tempfile.mkdtemp(prefix=f"it-{operation}-"))
        shutil.copytree(diverged, d, dirs_exist_ok=True, ignore=REPO_TEMPLATE_IGNORE)
        assert target == "main"
        assert _git(d, operation, "main").returncode != 0
        _INTERRUPTED_TEMPLATE_CACHE[operation] = (d, target, feature, main)
    template, target, feature, main = _INTERRUPTED_TEMPLATE_CACHE[operation]
    close_cached_repo(root)
    close_cached_repo(template)
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(template, root, ignore=REPO_TEMPLATE_IGNORE)
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=main,
            resolving_rebase=operation == "rebase",
            resolving_merge=operation == "merge",
        ),
    )
    return target, feature, main
