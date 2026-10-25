"""Tests for §7.5 outer-integration public-seam scenarios (a)-(c).

Driven through the real outer integration entry :func:`auto_integrate_after_commit`.
Asserts strictly on:
- Invocation order
- End state and outcome results
- Logged demotion reasons
No test reads private counters or accesses private ledger state.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.git.rebase.rebase import RebaseResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.integration_actions import AgentRunner

from git import Repo
from loguru import logger as loguru_logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.integration_actions import (
    install_executors,
    reset_executors,
)
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState, ResolutionFailureCause
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.timeout_seconds(5)

_TARGET = "main"


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
            }
        }
    )


def _setup_test_repo(root: Path) -> Path:
    """Set up a minimal git repo with diverged main and feature branches."""
    repo = Repo.init(root, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")
    (root / "shared.txt").write_text("initial seed\n", encoding="utf-8")
    repo.index.add(["shared.txt"])
    repo.index.commit("initial commit")
    feature = repo.create_head("feature")
    (root / "shared.txt").write_text("main conflict\n", encoding="utf-8")
    repo.index.add(["shared.txt"])
    repo.index.commit("main commit")
    repo.head.reference = feature
    repo.head.reset(index=True, working_tree=True)
    (root / "shared.txt").write_text("feature conflict\n", encoding="utf-8")
    repo.index.add(["shared.txt"])
    repo.index.commit("feature commit")
    return root


@pytest.fixture(scope="module")
def _template_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    template_dir = tmp_path_factory.mktemp("seed_repo")
    return _setup_test_repo(template_dir)


class _FakeGitOps:
    """In-memory GitOps seam implementation providing conflict rebase and fast-forward."""

    def __init__(self, *, rebase_conflicts: Sequence[str] = ("shared.txt",)) -> None:
        self.mutations: list[str] = []
        self._conflicted_files = tuple(rebase_conflicts)
        self.landed = True

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        del root, target
        self.mutations.append("rebase_onto")
        return RebaseConflicts(files=list(self._conflicted_files))

    def abort_rebase(self, root: Path) -> None:
        del root
        self.mutations.append("abort_rebase")

    def continue_rebase(self, root: Path) -> None:
        del root
        self.mutations.append("continue_rebase")

    def abort_merge(self, root: Path) -> bool:
        del root
        self.mutations.append("abort_merge")
        return True

    def continue_merge(self, root: Path) -> bool:
        del root
        self.mutations.append("continue_merge")
        return True

    def abort_cherry_pick(self, root: Path) -> bool:
        del root
        self.mutations.append("abort_cherry_pick")
        return True

    def continue_cherry_pick(self, root: Path) -> bool:
        del root
        self.mutations.append("continue_cherry_pick")
        return True

    def stage_all(self, root: Path) -> bool:
        del root
        self.mutations.append("stage_all")
        return True

    def commit(
        self,
        root: Path,
        *,
        on_failure: Callable[[str], None] | None = None,
    ) -> bool:
        del root, on_failure
        self.mutations.append("commit")
        return True

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: object = None,
    ) -> tuple[bool, str]:
        del root, target, feature_sha, reclaim_target_worktree, on_reclaimed
        self.mutations.append("fast_forward_target")
        return self.landed, "" if self.landed else "target advanced concurrently"

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: object = None,
    ) -> tuple[bool, str]:
        del root, target, feature_sha, reclaim_target_worktree, on_reclaimed
        self.mutations.append("land_feature")
        return self.landed, "" if self.landed else "target advanced concurrently"

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        del root, pre_feature_sha, owning_worktree
        self.mutations.append("create_backup_ref")
        return "refs/ralph-backup/feature"

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        del root, backup_ref
        self.mutations.append("delete_backup_ref")

    def reattach_head(self, root: Path, branch: str) -> bool:
        del root, branch
        self.mutations.append("reattach_head")
        return True


def _make_agent_runner(
    ledger: AgentFailureLedger,
    clock: FakeClock,
    candidates: Sequence[str],
    invoke_map: dict[str, Callable[[], bool]],
    invocations: list[str],
) -> AgentRunner:
    """Create an AgentRunner that selects and tracks candidates via public resolver health."""
    from ralph.pipeline.conflict_resolution.resolver_health import (
        bench_resolver,
        choose_resolver,
        clear_resolver,
    )
    from ralph.pipeline.conflict_resolution.session import ensure_session_controller

    class _ScenarioAgentRunner:
        def resolve_rebase(
            self,
            root: Path,
            target: str,
            resolver: RebaseStopResolver,
            *,
            session: ResolutionSession,
        ) -> bool:
            del root, target, resolver
            if session.ledger is None:
                session.ledger = ledger
            controller = ensure_session_controller(session, None)
            for _ in range(10):
                choice = choose_resolver(controller, candidates)
                if choice.agent is None:
                    return False
                agent = choice.agent
                invocations.append(agent)
                fn = invoke_map.get(agent)
                success = fn() if fn is not None else False
                if success:
                    clear_resolver(controller, agent)
                    return True
                bench_resolver(controller, agent, "unproductive")
            return False

    return _ScenarioAgentRunner()


def test_outer_integration_scenario_a_resolver_a_unproductive_three_times_b_resolves(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    """§7.5 Scenario (a): Resolver A fails three times (demoted), B resolves.

    The rebase succeeds and lands with no rebase-state loss.
    Asserts on:
    - invocation order: ["agentA", "agentA", "agentA", "agentB"]
    - result: RebaseOutcomeKind.REBASED, fast_forwarded is True
    - logged demotion reason: demotion logged in rebase_conflict_resolution phase.
    """
    shutil.copytree(_template_repo, tmp_path, dirs_exist_ok=True)
    invocations: list[str] = []

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    git_ops = _FakeGitOps()

    agent_runner = _make_agent_runner(
        ledger=ledger,
        clock=clock,
        candidates=("agentA", "agentB"),
        invoke_map={"agentA": lambda: False, "agentB": lambda: True},
        invocations=invocations,
    )
    token = install_executors(git_ops=git_ops, agent_runner=agent_runner, clock=clock)

    captured_logs: list[str] = []
    sink_id = loguru_logger.add(lambda msg: captured_logs.append(str(msg)), level="DEBUG")

    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
            rebase_stop_resolver=lambda *_a, **_kw: True,
            git_ops=git_ops,
            agent_runner=agent_runner,
            ledger=ledger,
            candidates=("agentA", "agentB"),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )

        # 1. Invocations: Agent A runs 3 times, is demoted, then Agent B runs and resolves
        assert invocations == ["agentA", "agentA", "agentA", "agentB"]

        # 2. Result: landed cleanly via rebase
        assert outcome is not None
        assert outcome.last_outcome == RebaseOutcomeKind.REBASED
        assert outcome.fast_forwarded is True

        # 3. Logged demotion reason for agentA in rebase_conflict_resolution
        assert any(
            "Agent 'agentA' demoted in phase 'rebase_conflict_resolution'" in line
            for line in captured_logs
        )
    finally:
        loguru_logger.remove(sink_id)
        reset_executors(token)


def test_outer_integration_scenario_b_all_agents_demoted_reports_pending_waiting_not_failed(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    """§7.5 Scenario (b): When every candidate is demoted, integration reports pending, not failed.

    No rebase state is lost, no revert is performed, and no operator handoff is triggered.
    """
    shutil.copytree(_template_repo, tmp_path, dirs_exist_ok=True)
    invocations: list[str] = []

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    # Demote both candidate agents in rebase_conflict_resolution phase before integration
    for _ in range(3):
        ledger.record(
            AttemptFact(phase="rebase_conflict_resolution", agent="agentA", artifact_valid=False)
        )
    for _ in range(3):
        ledger.record(
            AttemptFact(phase="rebase_conflict_resolution", agent="agentB", artifact_valid=False)
        )

    assert not ledger.is_available("rebase_conflict_resolution", "agentA")
    assert not ledger.is_available("rebase_conflict_resolution", "agentB")

    git_ops = _FakeGitOps()
    agent_runner = _make_agent_runner(
        ledger=ledger,
        clock=clock,
        candidates=("agentA", "agentB"),
        invoke_map={"agentA": lambda: False, "agentB": lambda: False},
        invocations=invocations,
    )
    token = install_executors(git_ops=git_ops, agent_runner=agent_runner, clock=clock)

    prior_state = RebaseState(
        last_target=_TARGET,
        conflict_strategy_index=1,
        conflict_strategies_tried=("strategy_0",),
    )

    captured_logs: list[str] = []
    sink_id = loguru_logger.add(lambda msg: captured_logs.append(str(msg)), level="DEBUG")

    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            prior_state,
            conflict_resolver=lambda *_a: False,
            rebase_stop_resolver=lambda *_a, **_kw: True,
            git_ops=git_ops,
            agent_runner=agent_runner,
            ledger=ledger,
            candidates=("agentA", "agentB"),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )

        # 1. No resolver invoked because all candidates are demoted
        assert invocations == []

        # 2. Rebase outcome is pending/waiting conflict, not failed
        assert outcome is not None
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
        assert outcome.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
        assert not outcome.is_intervention_required
        assert not outcome.fast_forwarded

        # 3. Rebase state is preserved
        assert outcome.last_target == _TARGET

        # 4. Resolver suppression logged
        assert any(
            "auto_integrate: conflict resolution budget exhausted" in line for line in captured_logs
        )
    finally:
        loguru_logger.remove(sink_id)
        reset_executors(token)


def test_outer_integration_scenario_b_all_agents_demoted_during_resolution_reports_pending(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    """§7.5 Scenario (b): When all candidates fail and become demoted during resolution.

    Integration reports pending/waiting, not failed, and rebase state is preserved.
    """
    shutil.copytree(_template_repo, tmp_path, dirs_exist_ok=True)
    invocations: list[str] = []

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    git_ops = _FakeGitOps()

    agent_runner = _make_agent_runner(
        ledger=ledger,
        clock=clock,
        candidates=("agentA", "agentB"),
        invoke_map={"agentA": lambda: False, "agentB": lambda: False},
        invocations=invocations,
    )
    token = install_executors(git_ops=git_ops, agent_runner=agent_runner, clock=clock)

    prior_state = RebaseState(
        last_target=_TARGET,
        conflict_strategy_index=0,
    )

    captured_logs: list[str] = []
    sink_id = loguru_logger.add(lambda msg: captured_logs.append(str(msg)), level="DEBUG")

    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            prior_state,
            conflict_resolver=lambda *_a: False,
            rebase_stop_resolver=lambda *_a, **_kw: True,
            git_ops=git_ops,
            agent_runner=agent_runner,
            ledger=ledger,
            candidates=("agentA", "agentB"),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )

        # 1. Invocations: agentA fails 3 times, agentB fails 3 times
        assert invocations == ["agentA", "agentA", "agentA", "agentB", "agentB", "agentB"]

        # 2. Outcome: unresolved conflict preserved as pending/waiting, not failed
        assert outcome is not None
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
        assert outcome.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
        assert not outcome.is_intervention_required
        assert not outcome.fast_forwarded

        # 3. Logged demotion reasons for both candidates
        assert any(
            "Agent 'agentA' demoted in phase 'rebase_conflict_resolution'" in line
            for line in captured_logs
        )
        assert any(
            "Agent 'agentB' demoted in phase 'rebase_conflict_resolution'" in line
            for line in captured_logs
        )
    finally:
        loguru_logger.remove(sink_id)
        reset_executors(token)


def test_outer_integration_scenario_c_demoted_integration_agent_preserves_other_phases_standing(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    """§7.5 Scenario (c) Direction 1: Demoting an agent in integration preserves standing in other phases."""
    shutil.copytree(_template_repo, tmp_path, dirs_exist_ok=True)
    invocations: list[str] = []

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    git_ops = _FakeGitOps()

    agent_runner = _make_agent_runner(
        ledger=ledger,
        clock=clock,
        candidates=("agentA",),
        invoke_map={"agentA": lambda: False},
        invocations=invocations,
    )
    token = install_executors(git_ops=git_ops, agent_runner=agent_runner, clock=clock)

    captured_logs: list[str] = []
    sink_id = loguru_logger.add(lambda msg: captured_logs.append(str(msg)), level="DEBUG")

    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
            rebase_stop_resolver=lambda *_a, **_kw: True,
            git_ops=git_ops,
            agent_runner=agent_runner,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )

        assert outcome is not None
        assert invocations == ["agentA", "agentA", "agentA"]

        # Agent A is demoted in rebase_conflict_resolution
        assert not ledger.is_available("rebase_conflict_resolution", "agentA")

        # But Agent A remains completely available in development, review, and planning phases
        assert ledger.is_available("development", "agentA")
        assert ledger.is_available("review", "agentA")
        assert ledger.is_available("planning", "agentA")

        # Demotion reason logged
        assert any(
            "Agent 'agentA' demoted in phase 'rebase_conflict_resolution'" in line
            for line in captured_logs
        )
    finally:
        loguru_logger.remove(sink_id)
        reset_executors(token)


def test_outer_integration_scenario_c_demoted_development_agent_preserves_integration_standing(
    tmp_path: Path,
    _template_repo: Path,
) -> None:
    """§7.5 Scenario (c) Direction 2: Demoting an agent in development does not affect integration standing."""
    shutil.copytree(_template_repo, tmp_path, dirs_exist_ok=True)
    invocations: list[str] = []

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    # 1. Demote agentA in development phase
    for _ in range(3):
        ledger.record(AttemptFact(phase="development", agent="agentA", artifact_valid=False))

    # Agent A is unavailable in development phase
    assert not ledger.is_available("development", "agentA")

    # But Agent A is available in rebase_conflict_resolution phase
    assert ledger.is_available("rebase_conflict_resolution", "agentA")

    git_ops = _FakeGitOps()
    agent_runner = _make_agent_runner(
        ledger=ledger,
        clock=clock,
        candidates=("agentA",),
        invoke_map={"agentA": lambda: True},
        invocations=invocations,
    )
    token = install_executors(git_ops=git_ops, agent_runner=agent_runner, clock=clock)

    try:
        # 2. auto_integrate_after_commit is called with candidate ("agentA",)
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
            rebase_stop_resolver=lambda *_a, **_kw: True,
            git_ops=git_ops,
            agent_runner=agent_runner,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )

        # Agent A was not blocked by its development demotion; it was invoked and resolved the rebase
        assert invocations == ["agentA"]
        assert outcome is not None
        assert outcome.last_outcome == RebaseOutcomeKind.REBASED
        assert outcome.fast_forwarded is True
    finally:
        reset_executors(token)
