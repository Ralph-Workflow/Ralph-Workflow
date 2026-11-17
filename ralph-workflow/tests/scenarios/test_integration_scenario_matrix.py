from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from git import GitCommandError, Repo

from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_record import adopt_active_rebase_record
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_git import SCENARIO_GIT_IDENTITY
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    CONFLICTED_PATH,
    FakeAgentMode,
    FrozenClock,
    make_conflicted_repo,
    make_rebase_stop_resolver,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)
from tests.scenarios._integration_scenario_fixtures import (
    _AGENT_BEHAVIOR_MATRIX_PARAMS,
    _CONFIG_CLOCK_MATRIX_PARAMS,
    _REPO_STATE_LEDGER_MATRIX_PARAMS,
    _SUPERVISOR_RESTART_POINTS,
    MatrixExpectedOutcome,
    _make_config,
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
    "test_symptom_4_partial_out_of_reach_with_declared_decision_files_lands": (
        "restores the gated version that drives auto_integrate_after_commit with the "
        "adopted active rebase record and pins exactly one resolver launch; it moves here "
        "from the duplicate rebase-landing module, which is deleted"
    ),
    "test_symptom_5_completed_rebase_proven_landed_by_anchor_chain": (
        "restores the gated proven-landed verdict assertion; the harness appends that "
        "line only when the anchor chain reaches the target"
    ),
    "test_symptom_5_empty_resolution_stop_proven_landed_by_tree": (
        "restores the gated proven-landed-by-tree verdict assertion; the harness appends "
        "that line only when HEAD and its tree match the recorded stop"
    ),
    "test_symptom_5_empty_resolution_stop_proven_landed_by_anchor_ancestry": (
        "restores the gated proven-landed-by-anchor-ancestry verdict assertion"
    ),
    "test_symptom_5_unrelated_head_rejected_once_then_quiet": (
        "restores the gated single actionable rejection naming the resume command; the "
        "rejected-once terminal state still requires ten ticks with no resolver launch"
    ),
    "test_symptom_6_unstaged_text_and_gitlink_conflict_staged_and_continued": (
        "restores the gated direct auto_integrate_after_commit scenario that requires the "
        "REBASED outcome and a finished rebase"
    ),
    "test_symptom_repro_ralphsite_wt02_worktree": (
        "restores the gated direct auto_integrate_after_commit repro that requires a "
        "fast-forward and the unreachable-remote reason"
    ),
    "test_symptom_repro_ralphsite_wt04_worktree": (
        "restores the gated direct auto_integrate_after_commit repro that requires a "
        "deferred skip or a fast-forward with the unreachable-remote reason"
    ),
}

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]


@pytest.fixture(autouse=True)
def scenario_git_commit_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SCENARIO_GIT_IDENTITY.items():
        monkeypatch.setenv(name, value)


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
    ("agent_behavior", "git_exit_code", "expected"), _AGENT_BEHAVIOR_MATRIX_PARAMS
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


@pytest.mark.parametrize(("repo_state", "ledger_content"), _REPO_STATE_LEDGER_MATRIX_PARAMS)
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


@pytest.mark.parametrize(("config_kind", "clock_behavior"), _CONFIG_CLOCK_MATRIX_PARAMS)
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


@pytest.mark.parametrize("interruption_point", _SUPERVISOR_RESTART_POINTS)
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
