from __future__ import annotations

import contextlib
import functools
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from git import GitCommandError, Repo
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_record import IntegrationRecord, adopt_active_rebase_record
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.landed_verdict import LandedVerdict, expected_landed_facts, landed_verdict
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_git import SCENARIO_GIT_IDENTITY
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    CONFLICTED_PATH,
    TARGET,
    FakeAgentMode,
    FrozenClock,
    make_rebase_stop_resolver,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)
from tests.scenarios._integration_scenario_fixtures import (
    _REPO_STATE_LEDGER_MATRIX_PARAMS,
    materialize_conflicted_repo,
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
    "test_symptom_2_cooling_resolver_chain_enters_waiting_state_without_spinning (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_symptom_2_spent_resolver_chain_enters_waiting_state_without_spinning with an unchanged body"
    ),
    "test_symptom_1_corrupted_agents_toml_falls_back_and_proceeds": (
        "reuses a cached diverged conflicted-repo template via materialize_conflicted_repo "
        "instead of git-init plus three commits per call; assertions unchanged"
    ),
    "test_symptom_2_cooling_resolver_chain_enters_waiting_state_without_spinning": (
        "reuses the cached conflicted-repo template; assertions unchanged"
    ),
    "test_symptom_3_completed_rebase_advances_record_and_promotes": (
        "reuses the cached conflicted-repo template; also absorbs the supervisor-restart "
        "assertion that a fresh ledger relaunches no resolver after a completed rebase"
    ),
    "test_symptom_4_partial_out_of_reach_with_declared_decision_files_lands": (
        "paused_partial_out_of_reach_rebase materializes a cached paused rebase; "
        "assertions still require one resolver launch and a finished landed stop"
    ),
    "test_symptom_5_completed_rebase_proven_landed_by_anchor_chain": (
        "paused_three_commit_first_stop_rebase materializes a cached paused rebase; "
        "proven-landed verdict assertion unchanged"
    ),
    "test_symptom_5_empty_resolution_stop_proven_landed_by_tree": (
        "KEEP: one empty-resolution stop pins the recorded-tree landing verdict and "
        "requires HEAD to equal the target"
    ),
    "test_symptom_5_empty_resolution_stop_proven_landed_by_anchor_ancestry": (
        "KEEP: restore a separate empty-resolution stop that pins the "
        "proven-landed-by-anchor-ancestry operator verdict"
    ),
    "test_symptom_5_unrelated_head_rejected_once_then_quiet": (
        "paused three-commit fixture is cached; rejected-once still requires ten quiet "
        "supervisor ticks with no resolver launch"
    ),
    "test_symptom_6_unstaged_text_and_gitlink_conflict_staged_and_continued": (
        "paused_sixth_symptom_unstaged_gitlink_rebase materializes a cached paused rebase; "
        "REBASED and finished-rebase assertions unchanged"
    ),
    "test_symptom_repro_ralphsite_wt02_worktree": (
        "setup_ralphsite_wt02_worktree materializes a cached paused rebase then re-seeds "
        "the ledger; fast-forward and unreachable-remote assertions unchanged"
    ),
    "test_symptom_repro_ralphsite_wt04_worktree": (
        "setup_ralphsite_wt04_worktree materializes a cached paused rebase then re-seeds "
        "the ledger; deferred-skip or fast-forward assertions unchanged"
    ),
    "test_matrix_axis_agent_behavior_x_git_exit": (
        "REMOVE: edits_and_stages is covered by symptom_1/3, never_stages by symptom_6, "
        "leaves_marker by test_rebase_recovery_matrix, non_zero_exit by symptom_2"
    ),
    "test_matrix_axis_repo_state_x_ledger_content": (
        "keeps only the spent_chain cell that pins agentA skipped and agentB launched; "
        "drops no-op cells that only asserted outcome is not None"
    ),
    "test_matrix_axis_config_x_clock": (
        "REMOVE: corrupted and valid config landing as REBASED is covered by symptom_1; "
        "clock_behavior never branched"
    ),
    "test_matrix_axis_supervisor_restart": (
        "REMOVE: folded into test_symptom_3_completed_rebase_advances_record_and_promotes "
        "so the completed-rebase fixture is paid once"
    ),
    "test_large_work_scenario_many_conflicts_no_budget_exhaustion": (
        "REMOVE: single-conflict REBASED with a raised consecutive_limit duplicates the "
        "successful auto_integrate path already pinned by symptom_1 and symptom_3"
    ),
}

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]


REBASE_RESOLUTION_PHASE = "rebase_conflict_resolution"


@contextmanager
def capture_selection_announcements() -> Iterator[list[str]]:
    announcements: list[str] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", {})
        content = str(record.get("message", ""))
        if "Selected agent" in content:
            announcements.append(content)

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        yield announcements
    finally:
        logger.remove(sink_id)


def assert_selected_agent_announcement_implies_launch(
    announcements: Sequence[str],
    launch_count: int,
    *,
    phase: str = REBASE_RESOLUTION_PHASE,
) -> None:
    prefix = f"Phase {phase}: Selected agent"
    selected = [line for line in announcements if prefix in line]
    if not selected:
        return
    assert launch_count >= 1, (
        f"a selection announcement for phase {phase} must imply an actual launch: "
        f"announced {selected}, launch count {launch_count}"
    )


@pytest.fixture(autouse=True)
def selection_announcements() -> Iterator[list[str]]:
    with capture_selection_announcements() as announcements:
        yield announcements


@functools.lru_cache(maxsize=2)
def _make_config(*, remote_enabled: bool = False) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": TARGET,
                "auto_integrate_remote_enabled": remote_enabled,
            }
        }
    )


@pytest.fixture(autouse=True)
def scenario_git_commit_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SCENARIO_GIT_IDENTITY.items():
        monkeypatch.setenv(name, value)


def test_symptom_1_corrupted_agents_toml_falls_back_and_proceeds(
    selection_announcements: list[str],
    tmp_path: Path,
) -> None:
    repo_dir = materialize_conflicted_repo(tmp_path / "repo")
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
    assert_selected_agent_announcement_implies_launch(selection_announcements, len(invocations))


def test_symptom_2_cooling_resolver_chain_enters_waiting_state_without_spinning(
    selection_announcements: list[str],
    tmp_path: Path,
) -> None:
    repo_dir = materialize_conflicted_repo(tmp_path / "repo")
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
    assert_selected_agent_announcement_implies_launch(selection_announcements, len(invocations))


def test_symptom_3_completed_rebase_advances_record_and_promotes(
    selection_announcements: list[str],
    tmp_path: Path,
) -> None:
    repo_dir = materialize_conflicted_repo(tmp_path / "repo")
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
        resumed = auto_integrate_after_commit(
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
    assert resumed is not None
    assert resumed.last_outcome in (
        RebaseOutcomeKind.REBASED,
        RebaseOutcomeKind.SKIPPED,
    )
    assert fresh_invocations == [], (
        "After supervisor restart, previously completed rebase must not redo stops"
    )
    assert_selected_agent_announcement_implies_launch(
        selection_announcements, len(invocations) + len(fresh_invocations)
    )


def test_symptom_6_unstaged_text_and_gitlink_conflict_staged_and_continued(
    selection_announcements: list[str],
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
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
    assert_selected_agent_announcement_implies_launch(selection_announcements, len(invocations))


def test_symptom_repro_ralphsite_wt02_worktree(
    selection_announcements: list[str],
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    setup_ralphsite_wt02_worktree(repo_dir, seed_ledger=True)
    cfg = _make_config(remote_enabled=True)

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
        config=cfg,
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            cfg,
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
    assert_selected_agent_announcement_implies_launch(selection_announcements, len(invocations))


def test_symptom_repro_ralphsite_wt04_worktree(
    selection_announcements: list[str],
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    setup_ralphsite_wt04_worktree(repo_dir, seed_ledger=True)
    cfg = _make_config(remote_enabled=True)

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
        config=cfg,
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            cfg,
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
    assert_selected_agent_announcement_implies_launch(selection_announcements, len(invocations))


@pytest.mark.parametrize(("repo_state", "ledger_content"), _REPO_STATE_LEDGER_MATRIX_PARAMS)
def test_matrix_axis_repo_state_x_ledger_content(
    selection_announcements: list[str],
    tmp_path: Path,
    repo_state: str,
    ledger_content: str,
) -> None:
    repo_dir = materialize_conflicted_repo(tmp_path / "repo")
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
        assert_selected_agent_announcement_implies_launch(selection_announcements, len(invocations))


def test_symptom_4_partial_out_of_reach_with_declared_decision_files_lands(
    selection_announcements: list[str],
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
    assert_selected_agent_announcement_implies_launch(selection_announcements, len(invocations))


def test_symptom_5_completed_rebase_proven_landed_by_anchor_chain(
    selection_announcements: list[str],
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    result = run_three_commit_rebase_landed_anchor_chain(repo_dir)

    assert result.resolver_launches >= 1, "the conflicted first stop needs one resolver launch"
    assert result.terminal_state == "landed"
    assert not rebase_in_progress(repo_dir)
    assert_selected_agent_announcement_implies_launch(
        selection_announcements, result.resolver_launches
    )
    verdict = [line for line in result.operator_lines if "proven landed" in line.content]
    assert verdict, "the operator must see one proven-landed line naming HEAD and anchors"


def test_symptom_5_empty_resolution_stop_proven_landed_by_tree(
    selection_announcements: list[str],
    tmp_git_repo: Path,
) -> None:
    from tests._scenario_git import git

    repo_dir = tmp_git_repo
    result = run_empty_resolution_stop_landed_by_tree(repo_dir)

    assert result.resolver_launches >= 1
    assert result.terminal_state == "landed"
    assert not rebase_in_progress(repo_dir)
    tree_verdict = [
        line for line in result.operator_lines if "proven landed by tree" in line.content
    ]
    assert tree_verdict, (
        "an empty-resolution stop must be proven landed by the recorded tree so the "
        "rebase is not withheld"
    )
    assert_selected_agent_announcement_implies_launch(
        selection_announcements, result.resolver_launches
    )
    head = git(repo_dir, "rev-parse", "HEAD")
    target = git(repo_dir, "rev-parse", "main")
    assert head == target, (
        "empty-resolution landing must also place HEAD on the target so anchor-ancestry "
        f"proof holds; HEAD={head} target={target}"
    )


def test_symptom_5_empty_resolution_stop_proven_landed_by_anchor_ancestry(
    selection_announcements: list[str],
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
    assert_selected_agent_announcement_implies_launch(
        selection_announcements, result.resolver_launches
    )


def test_symptom_5_unrelated_head_rejected_once_then_quiet(
    selection_announcements: list[str],
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    result = run_three_commit_rebase_unrelated_head_rejected_once(repo_dir)

    assert result.terminal_state == "rejected-once", (
        "an unrelated HEAD must produce exactly one actionable rejection and further "
        "supervisor ticks must stay quiet (no repeated CRITICAL/WARNING, no "
        "resolver selection)"
    )
    rejections = [line for line in result.operator_lines if "landing withheld" in line.content]
    assert len(rejections) == 1, (
        f"the unrelated HEAD must be rejected exactly once, got {len(rejections)}"
    )
    assert "resume with" in rejections[0].content
    assert_selected_agent_announcement_implies_launch(
        selection_announcements, result.resolver_launches
    )


def test_landed_proof_facts_name_the_proof_inputs_not_the_retired_three_sha_allowlist() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
        integrated_feature_sha="3" * 40,
        rebase_continue_stop="4" * 40,
        rebase_continue_head="5" * 40,
        rebase_continue_tree="6" * 40,
    )

    rendered = "; ".join(expected_landed_facts(record))

    assert "integrated_feature_sha=" not in rendered, (
        "the facts list must not render the retired three-SHA allowlist key integrated_feature_sha="
    )
    assert "pre_target_sha=" not in rendered, (
        "the facts list must not render the retired three-SHA allowlist key pre_target_sha="
    )
    assert "pre_feature_sha=" not in rendered, (
        "the facts list must not render the retired three-SHA allowlist key pre_feature_sha="
    )
    assert "advanced_feature_tip=" + "3" * 40 in rendered, (
        "the advanced-completion proof input must be named in the rendered facts"
    )
    assert "onto_tip=" + "2" * 40 in rendered, (
        "the rebased-range proof onto tip must be named in the rendered facts"
    )
    assert "feature_tip=" + "1" * 40 in rendered, (
        "the rebased-range proof feature tip must be named in the rendered facts"
    )
    assert "rebase_continue_stop=" + "4" * 40 in rendered
    assert "rebase_continue_head=" + "5" * 40 in rendered
    assert "rebase_continue_tree=" + "6" * 40 in rendered


def test_landed_verdict_projects_the_recorded_landing_decision(tmp_git_repo: Path) -> None:
    head = Repo(tmp_git_repo).head.commit.hexsha
    landed_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha=head,
        integrated_feature_sha=head,
    )
    unlanded_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha=None,
        integrated_feature_sha="0" * 40,
    )

    assert landed_verdict(tmp_git_repo, landed_record) is LandedVerdict.LANDED
    assert landed_verdict(tmp_git_repo, unlanded_record) is LandedVerdict.NOT_LANDED


def test_landed_verdict_is_unknown_when_the_head_cannot_be_read(tmp_path: Path) -> None:
    empty_repo = tmp_path / "empty"
    empty_repo.mkdir()
    Repo.init(empty_repo, initial_branch="main")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha=None,
    )

    assert landed_verdict(empty_repo, record) is LandedVerdict.UNKNOWN
