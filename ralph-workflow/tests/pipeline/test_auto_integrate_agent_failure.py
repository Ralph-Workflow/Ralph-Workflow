"""Tests for §7.5 outer-integration public-seam scenarios (a)-(c).

Driven through the real outer integration entry :func:`auto_integrate_after_commit`.
Asserts strictly on:
- Invocation order
- End state and outcome results
- Logged demotion reasons
No test reads private counters or accesses private ledger state.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.policy.models import PolicyBundle

import pytest
from loguru import logger as loguru_logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.merge import MergeResult
from ralph.git.rebase.rebase import RebaseResult, RebaseSuccess
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.sight import ConflictSight
from ralph.pipeline.integration_actions import (
    install_executors,
    reset_executors,
)

# Pre-empt circular import between git.merge and pipeline by importing rebase_state first
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState, ResolutionFailureCause
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.workspace.scope import WorkspaceScope

_CONFLICTED = ["src/alpha.py"]


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


_TARGET = "main"
_FEATURE_SHA = "feature-sha-1234"
_TARGET_SHA = "target-sha-5678"


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
            }
        }
    )


class _FakeGitOps:
    """In-memory GitOps seam implementation avoiding subprocess calls."""

    def __init__(self, rebase_result: RebaseResult | None = None) -> None:
        self.mutations: list[str] = []
        self._rebase_result = rebase_result or RebaseSuccess()
        self.landed = True

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        del root, target
        self.mutations.append("rebase_onto")
        return self._rebase_result

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
        del reclaim_target_worktree, on_reclaimed
        self.mutations.append("land_feature")
        return self.fast_forward_target(root, target, feature_sha)

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


class _ScenarioAgentRunner:
    """AgentRunner seam implementation driving the real conflict resolution pipeline."""

    def __init__(
        self,
        ledger: AgentFailureLedger,
        clock: FakeClock,
        invoke_fn: Callable[[str, Path, int], bool],
    ) -> None:
        self.ledger = ledger
        self.clock = clock
        self.invoke_fn = invoke_fn
        self.dispatches: list[str] = []

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        del resolver
        self.dispatches.append("resolve_rebase")
        if session.ledger is None:
            session.ledger = self.ledger
        return driver_module.run_conflict_resolution_pipeline(
            root=root,
            target=target,
            config=UnifiedConfig.model_validate({"general": {}}),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=self.invoke_fn,
            clock=self.clock.monotonic,
            session=session,
        )


@pytest.fixture
def _stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    record_store: dict[str, object] = {}
    import ralph.git.operations as go
    import ralph.pipeline.auto_integrate as ai
    import ralph.pipeline.auto_integrate_budget_seam as aibs
    import ralph.pipeline.auto_integrate_rebase_merge as arm
    import ralph.pipeline.auto_integrate_resolve as air

    monkeypatch.setattr(ai, "resolve_integration_target", lambda *_a, **_k: _TARGET)
    monkeypatch.setattr(ai, "current_branch_or_detached_marker", lambda *_a, **_k: "feature")
    monkeypatch.setattr(ai, "refresh_target", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "branch_sha", lambda *_a, **_k: _TARGET_SHA)
    monkeypatch.setattr(ai, "get_head_sha", lambda *_a, **_k: _FEATURE_SHA)
    monkeypatch.setattr(go, "get_head_sha", lambda *_a, **_k: _FEATURE_SHA)
    monkeypatch.setattr(ai, "check_rebase_preconditions", lambda *_a, **_k: None)
    monkeypatch.setattr(
        ai,
        "observe_conflict_identity",
        lambda *_a, **_k: ConflictIdentity(feature_sha=_FEATURE_SHA, target_sha=_TARGET_SHA),
    )
    monkeypatch.setattr(
        aibs,
        "observe_conflict_identity",
        lambda *_a, **_k: ConflictIdentity(feature_sha=_FEATURE_SHA, target_sha=_TARGET_SHA),
    )
    monkeypatch.setattr(arm, "range_routing_reason", lambda *_a, **_k: None)
    monkeypatch.setattr(arm, "rebase_in_progress", lambda _r: False)
    monkeypatch.setattr(arm, "verify_terminal_state", lambda *_a, **_k: None)
    monkeypatch.setattr(
        air, "merge_target_into_current", lambda *_a, **_k: MergeResult(outcome="conflict")
    )
    monkeypatch.setattr(ai, "recover_before_attempt", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "post_attempt_verify", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "contained_land", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "write_record", lambda _r, rec: record_store.update({"cur": rec}))
    monkeypatch.setattr(ai, "read_record", lambda *_a, **_k: record_store.get("cur"))
    monkeypatch.setattr(
        ai,
        "read_record_outcome",
        lambda *_a, **_k: RecordReadOutcome(disposition=RecordReadOutcomeDisposition.ABSENT),
    )
    monkeypatch.setattr(ai, "read_post_integration_head_sha", lambda *_a, **_k: _FEATURE_SHA)
    return record_store


def test_outer_integration_scenario_a_resolver_a_unproductive_three_times_b_resolves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _stubs: dict[str, object],
) -> None:
    """§7.5 Scenario (a): Resolver A fails three times (demoted), B resolves.

    The rebase succeeds and lands with no rebase-state loss.
    Asserts on:
    - invocation order: ["agentA", "agentA", "agentA", "agentB"]
    - result: RebaseOutcomeKind.REBASED, fast_forwarded is True
    - logged demotion reason: demotion logged in rebase_conflict_resolution phase.
    """
    del _stubs
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("agentA", "agentB"))

    resolved = False
    invocations: list[str] = []

    monkeypatch.setattr(
        driver_module, "unmerged_paths", lambda _r: [] if resolved else list(_CONFLICTED)
    )
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _r, paths: dict.fromkeys(paths, ConflictSight.AGENT),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda _r, paths: [] if resolved else list(paths),
    )

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        nonlocal resolved
        invocations.append(agent_name)
        if agent_name == "agentA":
            return False
        resolved = True
        return True

    clock = FakeClock()
    git_ops = _FakeGitOps(rebase_result=RebaseConflicts(files=["src/alpha.py"]))
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    agent_runner = _ScenarioAgentRunner(ledger=ledger, clock=clock, invoke_fn=_invoke)
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _stubs: dict[str, object],
) -> None:
    """§7.5 Scenario (b): When every candidate is demoted, integration reports pending, not failed.

    No rebase state is lost, no revert is performed, and no operator handoff is triggered.
    """
    del _stubs
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("agentA", "agentB"))

    invocations: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        return False

    clock = FakeClock()
    git_ops = _FakeGitOps(rebase_result=RebaseConflicts(files=["src/alpha.py"]))
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    # Demote both candidate agents in rebase_conflict_resolution phase
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

    agent_runner = _ScenarioAgentRunner(ledger=ledger, clock=clock, invoke_fn=_invoke)
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _stubs: dict[str, object],
) -> None:
    """§7.5 Scenario (b): When all candidates fail and become demoted during resolution.

    Integration reports pending/waiting, not failed, and rebase state is preserved.
    """
    del _stubs
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("agentA", "agentB"))

    invocations: list[str] = []

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _r: list(_CONFLICTED))
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _r, paths: dict.fromkeys(paths, ConflictSight.AGENT),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, paths: list(paths))

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        return False

    clock = FakeClock()
    # Prevent FakeClock from advancing monotonic time on sleep so agents remain cooling down
    monkeypatch.setattr(clock, "sleep", lambda _s: None)
    git_ops = _FakeGitOps(rebase_result=RebaseConflicts(files=["src/alpha.py"]))
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    agent_runner = _ScenarioAgentRunner(ledger=ledger, clock=clock, invoke_fn=_invoke)
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _stubs: dict[str, object],
) -> None:
    """§7.5 Scenario (c) Direction 1: Demoting an agent in integration preserves standing in other phases."""
    del _stubs
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("agentA",))

    invocations: list[str] = []

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _r: list(_CONFLICTED))
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _r, paths: dict.fromkeys(paths, ConflictSight.AGENT),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, paths: list(paths))

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        return False

    clock = FakeClock()
    monkeypatch.setattr(clock, "sleep", lambda _s: None)
    git_ops = _FakeGitOps(rebase_result=RebaseConflicts(files=["src/alpha.py"]))
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    agent_runner = _ScenarioAgentRunner(ledger=ledger, clock=clock, invoke_fn=_invoke)
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _stubs: dict[str, object],
) -> None:
    """§7.5 Scenario (c) Direction 2: Demoting an agent in development does not affect integration standing."""
    del _stubs
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _b: ("agentA",))

    resolved = False
    invocations: list[str] = []

    monkeypatch.setattr(
        driver_module, "unmerged_paths", lambda _r: [] if resolved else list(_CONFLICTED)
    )
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _r, paths: dict.fromkeys(paths, ConflictSight.AGENT),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _r, _k: ())
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda _r, paths: [] if resolved else list(paths),
    )

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        nonlocal resolved
        invocations.append(agent_name)
        resolved = True
        return True

    clock = FakeClock()
    git_ops = _FakeGitOps(rebase_result=RebaseConflicts(files=["src/alpha.py"]))
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

    # 1. Demote agentA in development phase
    for _ in range(3):
        ledger.record(AttemptFact(phase="development", agent="agentA", artifact_valid=False))

    # Agent A is unavailable in development phase
    assert not ledger.is_available("development", "agentA")

    # But Agent A is available in rebase_conflict_resolution phase
    assert ledger.is_available("rebase_conflict_resolution", "agentA")

    agent_runner = _ScenarioAgentRunner(ledger=ledger, clock=clock, invoke_fn=_invoke)
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
