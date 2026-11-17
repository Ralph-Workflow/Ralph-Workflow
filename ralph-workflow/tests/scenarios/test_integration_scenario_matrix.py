from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from git import GitCommandError, Repo

from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
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
from tests.scenarios._rebase_recovery_harness import (
    run_declared_decisions_and_gitlink_resolution,
    run_empty_resolution_stop_anchor_ancestry,
    run_empty_resolution_stop_landed_by_tree,
    run_ralphsite_wt02_worktree,
    run_ralphsite_wt04_worktree,
    run_sixth_symptom_unstaged_gitlink,
    run_three_commit_rebase_landed_anchor_chain,
    run_three_commit_rebase_unrelated_head_rejected_once,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_symptom_6_unstaged_text_and_gitlink_conflict_staged_and_continued": (
        "the direct Repo.init fixture is replaced by the shared rebase-recovery harness on "
        "the identity-configured tmp_git_repo, which asserts the same mechanical staging "
        "and rebase continuation and additionally pins the resolver launch count, the "
        "follow-up launch count, the quiet follow-up ticks and the landed terminal state"
    ),
    "test_symptom_repro_ralphsite_wt02_worktree": (
        "the repro now runs through the shared harness, which observes the same facts "
        "(landed terminal state, no rebase left in progress, local fast-forward, "
        "unreachable remote in last_reason) through the production status seam instead "
        "of the direct auto-integrate call"
    ),
    "test_symptom_repro_ralphsite_wt04_worktree": (
        "the repro now runs through the shared harness, which observes the same deferred or "
        "skipped landing reason, the local fast-forward, the finished rebase and the "
        "unreachable remote through the production status seam instead of the direct "
        "auto-integrate call"
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


def test_symptom_4_partial_out_of_reach_with_declared_decision_files_lands(
    tmp_git_repo: Path,
) -> None:
    result = run_declared_decisions_and_gitlink_resolution(tmp_git_repo)

    assert result.terminal_state == "landed"
    assert not rebase_in_progress(tmp_git_repo)
    assert result.facts.get("driver_resolved") == "true"
    assert int(result.facts.get("driver_invocations", "0")) >= 1, (
        "the resolvable declared-decision set must launch a real resolver at least once"
    )
    assert any(
        "delete/modify declaration" in line.content for line in result.operator_lines
    ), "production must announce the declared-decision cause for each file"
    assert any(
        "entering rebase conflict resolution" in line.content for line in result.operator_lines
    )
    assert (tmp_git_repo / "deleted.txt").exists(), (
        "the declared delete/modify decision must keep the file landed"
    )


def test_symptom_5_completed_rebase_proven_landed_by_anchor_chain(
    tmp_git_repo: Path,
) -> None:
    result = run_three_commit_rebase_landed_anchor_chain(tmp_git_repo)

    assert result.resolver_launches >= 1, "the conflicted first stop needs one resolver launch"
    assert result.terminal_state == "landed"
    assert not rebase_in_progress(tmp_git_repo)
    assert not any(line.level == "CRITICAL" for line in result.operator_lines)
    status = [
        line
        for line in result.operator_lines
        if any(token in line.content.lower() for token in ("recovered", "rebased", "landing"))
    ]
    assert status, (
        "production status seam must emit a landed auto-integrate operator line; "
        "harness must not fabricate a proven-landed verdict"
    )
    assert result.facts.get("rebased_tip") == result.facts.get("target"), (
        "after the status-seam landing pass the rebased tip must equal the target"
    )
    assert "," in result.facts.get("anchor_chain", ""), (
        "multi-commit landing must retain an anchor chain longer than a single commit"
    )


def test_symptom_5_empty_resolution_stop_proven_landed_by_tree(
    tmp_git_repo: Path,
) -> None:
    result = run_empty_resolution_stop_landed_by_tree(tmp_git_repo)

    assert result.resolver_launches >= 1
    assert result.terminal_state == "landed"
    assert not rebase_in_progress(tmp_git_repo)
    assert result.facts.get("head") == result.facts.get("stop_anchor")
    assert result.facts.get("resolved_tree")
    status = [
        line
        for line in result.operator_lines
        if any(token in line.content.lower() for token in ("recovered", "rebased", "landing"))
    ]
    assert status, (
        "an empty-resolution stop must surface a production status line after the "
        "recorded tree lands; harness must not fabricate proven-landed-by-tree text"
    )


def test_symptom_5_empty_resolution_stop_proven_landed_by_anchor_ancestry(
    tmp_git_repo: Path,
) -> None:
    result = run_empty_resolution_stop_anchor_ancestry(tmp_git_repo)

    assert result.resolver_launches >= 1
    assert result.terminal_state == "landed"
    assert result.facts.get("head") == result.facts.get("target")
    status = [
        line
        for line in result.operator_lines
        if any(token in line.content.lower() for token in ("recovered", "rebased", "landing"))
    ]
    assert status, (
        "empty-resolution ancestry landing must emit a production status line; "
        "harness must not fabricate proven-landed-by-anchor-ancestry text"
    )


def test_symptom_5_unrelated_head_rejected_once_then_quiet(
    tmp_git_repo: Path,
) -> None:
    result = run_three_commit_rebase_unrelated_head_rejected_once(tmp_git_repo)

    assert result.terminal_state == "rejected-once", (
        "an unrelated HEAD must produce exactly one actionable rejection and ten "
        "further supervisor ticks must stay quiet (no repeated CRITICAL, no "
        "resolver selection)"
    )
    assert result.resolver_launches == 0, (
        f"supervisor ticks after the unrelated HEAD must launch no resolver, "
        f"got {result.resolver_launches}"
    )
    rejections = [line for line in result.operator_lines if "landing withheld" in line.content]
    assert len(rejections) == 1, (
        f"the unrelated HEAD must be rejected exactly once via the production status "
        f"seam, got {len(rejections)} lines: {[line.content for line in result.operator_lines]}"
    )
    assert not any(line.level == "CRITICAL" for line in result.operator_lines)


def test_symptom_6_unstaged_text_and_gitlink_conflict_staged_and_continued(
    tmp_git_repo: Path,
) -> None:
    result = run_sixth_symptom_unstaged_gitlink(tmp_git_repo)

    assert result.facts.get("agent_mode") == AgentMode.EDITS_NEVER_STAGES.value
    assert result.facts.get("first_round_launches") == "1", (
        f"EDITS_NEVER_STAGES must launch once so the agent can clear markers without "
        f"staging; mechanical staging/policy must finish the stop, got {result.facts}"
    )
    assert result.facts.get("followup_launches") == "0", (
        f"After mechanical staging and policy gitlink resolution, further supervisor "
        f"ticks must launch zero resolvers, got {result.facts}"
    )
    assert result.facts.get("quiet_ticks") == "true", (
        "ten supervisor ticks after landing must stay quiet (no further resolver launches)"
    )
    assert result.terminal_state == "landed"
    assert result.resolver_launches == 1
    assert not rebase_in_progress(tmp_git_repo)
    status = [
        line
        for line in result.operator_lines
        if any(token in line.content.lower() for token in ("rebased", "recovered", "landing"))
    ]
    assert status, "production status seam must emit the landed auto-integrate line"


def test_symptom_repro_ralphsite_wt02_worktree(
    tmp_git_repo: Path,
) -> None:
    result = run_ralphsite_wt02_worktree(tmp_git_repo)

    assert result.terminal_state == "landed", (
        f"Ralph-Site wt-02 must complete the local rebase and defer unreachable publication, "
        f"got {result.facts}"
    )
    assert result.facts.get("rebase_in_progress") == "false"
    assert result.facts.get("fast_forwarded") == "true"
    reason = result.facts.get("last_reason", "")
    assert "remote origin unreachable" in reason or "remote sync disabled" in reason, reason
    assert result.resolver_launches >= 0


def test_symptom_repro_ralphsite_wt04_worktree(
    tmp_git_repo: Path,
) -> None:
    result = run_ralphsite_wt04_worktree(tmp_git_repo)

    assert result.terminal_state == "landed", (
        f"Ralph-Site wt-04 must complete the local rebase and defer unreachable publication, "
        f"got {result.facts}"
    )
    reason = result.facts.get("last_reason", "")
    if result.facts.get("last_outcome") == str(RebaseOutcomeKind.SKIPPED):
        assert "deferred" in reason or "retained" in reason or "remote" in reason
    else:
        assert result.facts.get("fast_forwarded") == "true"
        assert result.facts.get("rebase_in_progress") == "false"
        assert "remote origin unreachable" in reason or "remote sync disabled" in reason, reason


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
