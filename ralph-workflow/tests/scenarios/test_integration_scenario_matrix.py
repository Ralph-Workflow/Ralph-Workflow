from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import GitCommandError, Repo

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    SupervisionInfrastructureError,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_record import adopt_active_rebase_record
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.attempt_facts import (
    AttemptFacts,
    TerminationIssuer,
    TerminationRecord,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    CONFLICTED_PATH,
    TARGET,
    FakeAgentMode,
    FrozenClock,
    make_conflicted_repo,
    make_rebase_stop_resolver,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)
from tests.scenarios._ralphsite_worktree_copy import (
    setup_ralphsite_wt02_worktree,
    setup_ralphsite_wt04_worktree,
)
from tests.scenarios._rebase_recovery_harness import (
    run_empty_resolution_stop_anchor_ancestry,
    run_empty_resolution_stop_landed_by_tree,
    run_three_commit_rebase_landed_anchor_chain,
    run_three_commit_rebase_unrelated_head_rejected_once,
)
from tests.scenarios._rebase_recovery_harness_helpers import (
    paused_partial_out_of_reach_rebase,
)
from tests.scenarios._sixth_symptom_unstaged_gitlink import (
    paused_sixth_symptom_unstaged_gitlink_rebase,
)

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

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.policy.models import PolicyBundle


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


def test_symptom_1_corrupted_agents_toml_falls_back_and_proceeds(
    tmp_path: Path,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    agents_toml = repo_dir / "ralph-workflow-agents.toml"
    agents_toml.write_text("corrupted [toml syntax invalid == \n", encoding="utf-8")

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED
    assert "agentA" in invocations


def test_symptom_2_spent_resolver_chain_enters_waiting_state_without_spinning(
    tmp_path: Path,
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
        agent_modes={"agentA": AgentMode.NON_ZERO_EXIT, "agentB": AgentMode.NON_ZERO_EXIT},
    )
    bundle = resolution_policy_bundle(candidates)
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=candidates,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert invocations == ["agentA", "agentB"], (
        f"Each failing agent must run exactly once before waiting, got {invocations}"
    )
    assert outcome is not None
    assert not outcome.is_intervention_required


def test_symptom_3_completed_rebase_advances_record_and_promotes(
    tmp_path: Path,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    repo = Repo(repo_dir)
    with contextlib.suppress(GitCommandError):
        repo.git.rebase("main")
    (repo_dir / CONFLICTED_PATH).write_text("clean resolution\n", encoding="utf-8")
    repo.git.add(CONFLICTED_PATH)
    repo.git.rebase("--continue", env={"GIT_EDITOR": "true"})

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=(),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert invocations == [], "Completed rebase must not invoke any resolver agent"
    assert outcome.last_outcome in (
        RebaseOutcomeKind.REBASED,
        RebaseOutcomeKind.SKIPPED,
    ), (
        f"A completed rebase must be proven landed and promoted to a terminal "
        f"landed/skipped record, got {outcome.last_outcome}"
    )


def test_symptom_6_unstaged_text_and_gitlink_conflict_staged_and_continued(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    Repo.init(repo_dir, initial_branch="main")
    paused_sixth_symptom_unstaged_gitlink_rebase(repo_dir)

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
        mode=FakeAgentMode.EDITS_NEVER_STAGES,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED, (
        f"Auto-rebase must stage marker-free edits automatically and finish rebase, "
        f"got {outcome.last_outcome}"
    )
    assert not rebase_in_progress(repo_dir)


def test_symptom_repro_ralphsite_wt02_worktree(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    Repo.init(repo_dir, initial_branch="main")
    setup_ralphsite_wt02_worktree(repo_dir, seed_ledger=True)

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
        mode=FakeAgentMode.EDITS_AND_STAGES,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert outcome.last_outcome in (
        RebaseOutcomeKind.RECOVERED,
        RebaseOutcomeKind.REBASED,
    ), (
        f"Ralph-Site wt-02 must complete the local rebase and defer unreachable publication, "
        f"got {outcome.last_outcome}"
    )
    assert outcome.fast_forwarded, "Ralph-Site wt-02 must fast-forward the resolved local branch"
    assert outcome.last_reason is not None and "remote origin unreachable" in outcome.last_reason
    assert not rebase_in_progress(repo_dir)


def test_symptom_repro_ralphsite_wt04_worktree(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    Repo.init(repo_dir, initial_branch="main")
    setup_ralphsite_wt04_worktree(repo_dir, seed_ledger=True)

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
        mode=FakeAgentMode.EDITS_AND_STAGES,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert outcome.last_outcome in (
        RebaseOutcomeKind.RECOVERED,
        RebaseOutcomeKind.REBASED,
        RebaseOutcomeKind.SKIPPED,
    ), (
        f"Ralph-Site wt-04 must complete the local rebase and defer unreachable publication, "
        f"got {outcome.last_outcome}"
    )
    if outcome.last_outcome is RebaseOutcomeKind.SKIPPED:
        assert outcome.last_reason is not None
        assert "deferred" in outcome.last_reason or "retained" in outcome.last_reason
    else:
        assert outcome.fast_forwarded, (
            "Ralph-Site wt-04 must fast-forward the resolved local branch"
        )
        assert (
            outcome.last_reason is not None and "remote origin unreachable" in outcome.last_reason
        )
        assert not rebase_in_progress(repo_dir)


@pytest.mark.parametrize(
    ("agent_behavior", "git_exit_code", "expected"),
    [
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
    ],
)
def test_matrix_axis_agent_behavior_x_git_exit(
    tmp_path: Path,
    agent_behavior: FakeAgentMode,
    git_exit_code: int,
    expected: MatrixExpectedOutcome,
) -> None:
    del git_exit_code
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    candidates = ("agentA", "agentB")
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA", "agentB"),
        tmp_path=repo_dir,
        mode=agent_behavior,
    )
    bundle = resolution_policy_bundle(candidates)
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=candidates,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    if expected.terminal_kind == "rebased":
        assert outcome.last_outcome == RebaseOutcomeKind.REBASED
    else:
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT


@pytest.mark.parametrize(
    ("repo_state", "ledger_content"),
    [
        ("clean", "empty_ledger"),
        ("detached_head", "empty_ledger"),
        ("unstaged_edits", "spent_chain"),
        ("unmerged_markers", "exhausted_budget"),
    ],
)
def test_matrix_axis_repo_state_x_ledger_content(
    tmp_path: Path,
    repo_state: str,
    ledger_content: str,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    if ledger_content == "spent_chain":
        ledger.record(
            AttemptFact(phase="rebase_conflict_resolution", agent="agentA", artifact_valid=False)
        )

    invocations: list[str] = []
    candidates = ("agentA", "agentB")
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentB",),
        tmp_path=repo_dir,
        mode=FakeAgentMode.EDITS_NEVER_STAGES
        if repo_state == "unstaged_edits"
        else FakeAgentMode.EDITS_AND_STAGES,
    )
    bundle = resolution_policy_bundle(candidates)
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=candidates,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    if ledger_content == "spent_chain":
        assert "agentA" not in invocations
        assert "agentB" in invocations


@pytest.mark.parametrize(
    ("config_kind", "clock_behavior"),
    [
        ("valid", "frozen"),
        ("corrupted_agents_toml", "frozen"),
        ("valid", "advancing"),
    ],
)
def test_matrix_axis_config_x_clock(
    tmp_path: Path,
    config_kind: str,
    clock_behavior: str,
) -> None:
    del clock_behavior
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    if config_kind == "corrupted_agents_toml":
        (repo_dir / "ralph-workflow-agents.toml").write_text("corrupted [[[\n", encoding="utf-8")

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED


@pytest.mark.parametrize(
    "interruption_point",
    [
        "mid_rebase",
        "post_resolution_pre_continue",
    ],
)
def test_matrix_axis_supervisor_restart(
    tmp_path: Path,
    interruption_point: str,
) -> None:
    del interruption_point
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    fresh_ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    fresh_invocations: list[str] = []
    fresh_invoke = make_scripted_resolver_process(
        invocations=fresh_invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
    )
    fresh_resolver = make_rebase_stop_resolver(
        ledger=fresh_ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=fresh_invoke,
        config=_make_config(),
    )

    fresh_token = install_executors(clock=clock)
    try:
        resumed_outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=fresh_resolver,
            ledger=fresh_ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(fresh_token)

    assert resumed_outcome is not None
    assert resumed_outcome.last_outcome in (
        RebaseOutcomeKind.REBASED,
        RebaseOutcomeKind.SKIPPED,
    )
    assert fresh_invocations == [], (
        "After supervisor restart, previously completed rebase must not redo stops"
    )


def test_large_work_scenario_many_conflicts_no_budget_exhaustion(
    tmp_path: Path,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=10)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED


def test_symptom_4_partial_out_of_reach_with_declared_decision_files_lands(
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    stages = paused_partial_out_of_reach_rebase(repo_dir)
    assert len(stages) == 3, "the gitlink must sit in the index at three stages"
    adopt_active_rebase_record(repo_dir, "main")

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
        mode=FakeAgentMode.EDITS_NEVER_STAGES,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert invocations.count("agentA") == 1, (
        f"The resolvable set is one declared-decision file; the resolver must launch "
        f"exactly once for it, got {invocations}"
    )
    assert not rebase_in_progress(repo_dir), (
        "After the reachable set is resolved and the gitlink is settled by policy, "
        "the rebase must finish"
    )
    assert outcome is not None
    assert outcome.last_outcome in (
        RebaseOutcomeKind.REBASED,
        RebaseOutcomeKind.RECOVERED,
        RebaseOutcomeKind.SKIPPED,
    ), (
        f"The stop must land through the declared-decision resolution plus the policy "
        f"gitlink, or wait only on an external condition, got {outcome.last_outcome}"
    )
    head = repo_dir / "deleted.txt"
    assert head.exists(), "the declared delete/modify decision must keep the file landed"
    assert not rebase_in_progress(repo_dir)


def test_symptom_5_completed_rebase_proven_landed_by_anchor_chain(
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    result = run_three_commit_rebase_landed_anchor_chain(repo_dir)

    assert result.resolver_launches >= 1, "the conflicted first stop needs one resolver launch"
    assert result.terminal_state == "landed"
    assert not rebase_in_progress(repo_dir)
    verdict = [line for line in result.operator_lines if "proven landed" in line.content]
    assert verdict, "the operator must see one proven-landed line naming HEAD and anchors"


def test_symptom_5_empty_resolution_stop_proven_landed_by_tree(
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    result = run_empty_resolution_stop_landed_by_tree(repo_dir)

    assert result.resolver_launches >= 1
    assert result.terminal_state == "landed"
    assert not rebase_in_progress(repo_dir)
    verdict = [line for line in result.operator_lines if "proven landed by tree" in line.content]
    assert verdict, (
        "an empty-resolution stop must be proven landed by the recorded tree so the "
        "rebase is not withheld"
    )


def test_symptom_5_empty_resolution_stop_proven_landed_by_anchor_ancestry(
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    result = run_empty_resolution_stop_anchor_ancestry(repo_dir)

    assert result.resolver_launches >= 1
    assert result.terminal_state == "landed"
    verdict = [
        line for line in result.operator_lines if "proven landed by anchor ancestry" in line.content
    ]
    assert verdict


def test_symptom_5_unrelated_head_rejected_once_then_quiet(
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    result = run_three_commit_rebase_unrelated_head_rejected_once(repo_dir)

    assert result.terminal_state == "rejected-once", (
        "an unrelated HEAD must produce exactly one actionable rejection and ten "
        "further supervisor ticks must stay quiet (no repeated CRITICAL/WARNING, no "
        "resolver selection)"
    )
    rejections = [line for line in result.operator_lines if "landing withheld" in line.content]
    assert len(rejections) == 1, (
        f"the unrelated HEAD must be rejected exactly once, got {len(rejections)}"
    )
    assert "resume with" in rejections[0].content


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
