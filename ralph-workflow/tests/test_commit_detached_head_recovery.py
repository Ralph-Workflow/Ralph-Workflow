"""Tests for detached-HEAD commit recovery (AC10, AC11).

Verifies that detached dirty/staged work attaches safely to the verified feature branch
before commit, repairable Git state routes to automated recovery, and it never reaches
failed_terminal or reports completed for a failed commit.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from ralph.pipeline.commit_executor import execute_commit_effect, set_last_commit_error
from ralph.pipeline.commit_state import CommitState
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

pytestmark = pytest.mark.timeout_seconds(5)

_MODULE_REPO_TEMPLATE: list[Path] = []


@pytest.fixture(scope="module", autouse=True)
def _init_repo_template(tmp_path_factory: pytest.TempPathFactory) -> None:
    td = tmp_path_factory.mktemp("dhead_template")
    from ralph.git.subprocess_runner import run_git

    run_git(("init",), cwd=td, label="test-repo-init")
    repo = Repo(td)
    repo.config_writer().set_value("user", "name", "Test Committer").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    writer = repo.config_writer()
    writer.set_value("gc", "auto", "0")
    writer.set_value("core", "fsync", "none")
    writer.release()
    actor = Actor("Test Committer", "test@example.com")

    # Initial commit on main
    base_file = td / "README.md"
    base_file.write_text("# Test Repo\n", encoding="utf-8")
    gitignore_file = td / ".gitignore"
    gitignore_file.write_text(".agent/\n", encoding="utf-8")
    repo.index.add(["README.md", ".gitignore"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    repo.git.branch("-M", "main")

    # Create feature branch
    repo.git.checkout("-b", "feature/work")
    feature_file = td / "feature.txt"
    feature_file.write_text("feature base\n", encoding="utf-8")
    repo.index.add(["feature.txt"])
    repo.index.commit("feature base commit", author=actor, committer=actor)
    repo.close()

    _MODULE_REPO_TEMPLATE.append(td)


def _make_test_repo(repo_root: Path, feature_branch: str = "feature/work") -> Repo:
    repo_root.mkdir(parents=True, exist_ok=True)
    if _MODULE_REPO_TEMPLATE:
        shutil.copytree(_MODULE_REPO_TEMPLATE[0], repo_root, dirs_exist_ok=True)
        repo = Repo(repo_root)
        if feature_branch != "feature/work":
            repo.git.checkout("-B", feature_branch)
        return repo

    from ralph.git.subprocess_runner import run_git

    run_git(("init",), cwd=repo_root, label="test-repo-init")
    repo = Repo(repo_root)
    repo.config_writer().set_value("user", "name", "Test Committer").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    writer = repo.config_writer()
    writer.set_value("gc", "auto", "0")
    writer.set_value("core", "fsync", "none")
    writer.release()
    actor = Actor("Test Committer", "test@example.com")

    # Initial commit on main
    base_file = repo_root / "README.md"
    base_file.write_text("# Test Repo\n", encoding="utf-8")
    gitignore_file = repo_root / ".gitignore"
    gitignore_file.write_text(".agent/\n", encoding="utf-8")
    repo.index.add(["README.md", ".gitignore"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    repo.git.branch("-M", "main")

    # Create feature branch
    repo.git.checkout("-b", feature_branch)
    feature_file = repo_root / "feature.txt"
    feature_file.write_text("feature base\n", encoding="utf-8")
    repo.index.add(["feature.txt"])
    repo.index.commit("feature base commit", author=actor, committer=actor)

    return repo


def _make_commit_artifact(repo_root: Path, message: str) -> Path:
    artifact_path = repo_root / ".agent" / "artifacts" / "commit_message.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        f"---\ntype: commit\nsubject: {message}\n---\n",
        encoding="utf-8",
    )
    return artifact_path


def _load_pipeline_policy() -> PipelinePolicy:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir).pipeline


def test_worktree_on_detached_head_commits_to_feature_branch_without_failed_terminal(
    tmp_path: Path,
) -> None:
    repo = _make_test_repo(tmp_path, "feature/work")
    feature_commit_sha = repo.head.commit.hexsha

    # Detach HEAD at the feature commit
    repo.git.checkout(feature_commit_sha)
    assert repo.head.is_detached

    # Add uncommitted tracked changes
    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: implement feature work")

    # Execute commit effect with branch specified in opts
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="feature/work",
    )

    # Must succeed, reattach to feature/work, and commit onto feature branch
    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature/work"
    assert repo.head.commit.message.splitlines()[0] == "feat: implement feature work"

    # Feed the event into reducer and ensure it advances cleanly without reaching failed_terminal
    policy = _load_pipeline_policy()
    state = PipelineState(phase="development_commit", previous_phase="development_commit_cleanup")
    next_state, _ = reduce(state, event, policy)

    assert next_state.phase != "failed_terminal"
    assert next_state.phase in ("development_analysis", "development_final_commit_cleanup")


def test_worktree_on_detached_head_reattaches_without_explicit_branch_arg(
    tmp_path: Path,
) -> None:
    """AC10 production-seam proof: detached HEAD is reattached without ``branch=``.

    The production ``runner._execute_commit_effect_from_deps`` does
    NOT pass ``branch=`` to ``execute_commit_effect`` (it forwards a
    fixed set of dependencies). The reattach helper must therefore
    resolve the target branch from the reflog, the worktree
    metadata, the configured target, or the repository name. This
    test reproduces the production seam exactly: a detached HEAD
    ahead of a feature branch, with uncommitted tracked changes,
    invoked without any ``branch=`` opt, must commit onto the
    feature branch and reach the next phase. The detached commit
    is never silently orphaned.
    """
    from ralph.config.models import UnifiedConfig

    repo = _make_test_repo(tmp_path, "feature/work")
    feature_commit_sha = repo.head.commit.hexsha

    # Detach HEAD at the feature commit (the same shape the
    # production seam leaves behind after a recovery reset or
    # snapshot).
    repo.git.checkout(feature_commit_sha)
    assert repo.head.is_detached

    # Add uncommitted tracked changes.
    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: production seam")

    # Production seam: no ``branch=`` opt. The resolver consults
    # the reflog and the configured target. The feature branch
    # is the ONLY branch in the reflog as a checkout destination
    # (the test only switches to ``feature/work`` and then
    # detaches), so the reattach must land on ``feature/work``
    # rather than on ``main``.
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        config=config,
    )

    # Must succeed: HEAD is reattached, the commit lands on the
    # feature branch, and the detached commit is preserved.
    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached, "HEAD must be reattached"
    assert repo.active_branch.name == "feature/work"
    # The new commit's parent is the prior feature tip (the
    # detached commit is preserved on the branch).
    assert repo.head.commit.parents[0].hexsha == feature_commit_sha
    assert repo.head.commit.message.splitlines()[0] == "feat: production seam"


def test_commit_failure_routes_to_automated_recovery_not_failed_terminal() -> None:
    policy = _load_pipeline_policy()
    state = PipelineState(
        phase="development_commit",
        previous_phase="development_commit_cleanup",
        last_error="GitOperationError: index.lock contention",
        pending_cycle_outcome="completed",
    )
    set_last_commit_error("GitOperationError: index.lock contention")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # Routes to automated recovery (development_commit_cleanup) instead of failed_terminal
        assert next_state.phase == "development_commit_cleanup"
        assert next_state.phase != "failed_terminal"
        # Phase display must NOT report 'completed' when a commit fails!
        assert next_state.pending_cycle_outcome is None
    finally:
        set_last_commit_error(None)


def test_commit_failure_clears_pending_completed_outcome() -> None:
    policy = _load_pipeline_policy()
    state = PipelineState(
        phase="development_commit",
        pending_cycle_outcome="completed",
        last_error="detached HEAD residue detected",
    )
    set_last_commit_error("detached HEAD residue detected")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # Must never report completed when commit failed
        assert next_state.pending_cycle_outcome is None
        assert next_state.phase != "failed_terminal"
    finally:
        set_last_commit_error(None)


def test_exhausted_commit_failure_routes_to_automated_recovery_not_terminal() -> None:
    """A repairable commit failure exhausted the bounded counter.

    The bounded counter protects against an infinite recovery loop
    but MUST NOT turn a repairable failure into a terminal one:
    Ralph owns the recovery path. The repairable failure is routed
    to the policy's commit-recovery phase (the distinct automated
    resolver) even when the counter is exhausted. Once the raw
    next increment would exceed the declared cap, the epoch is
    clamped at the cap and the recovery phase is still invoked.
    The phase display MUST NOT report 'completed' for a commit
    that failed and the route MUST NEVER be ``failed_terminal``
    for a repairable Git state.
    """
    # Hardcoded mirror of ``ralph.pipeline.reducer._MAX_COMMIT_RECOVERY_ATTEMPTS``
    # (= 3). The reducer is the only owner of that cap; the test pins the
    # boundary so a runtime cap bump is a deliberate, single-site change.
    # Importing the private symbol would re-anchor a private-ralph import
    # this test does not need.
    cap = 3
    policy = _load_pipeline_policy()
    # When recovery epoch is already at the cap (exhausted).
    state = PipelineState(
        phase="development_commit",
        recovery_epoch=cap,
        pending_cycle_outcome="completed",
        last_error="repeated commit failure",
    )
    set_last_commit_error("repeated commit failure")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # The phase must NEVER be failed_terminal for a repairable
        # commit failure, and the phase display must NOT report
        # 'completed' for a commit that failed.
        assert next_state.phase != "failed_terminal"
        assert next_state.phase in (
            "commit_recovery",
            "development_commit_cleanup",
        )
        assert next_state.pending_cycle_outcome is None
        # The counter is bounded: once the next increment would
        # exceed the cap, the epoch is clamped at the cap rather
        # than growing past it.
        assert next_state.recovery_epoch == cap
    finally:
        set_last_commit_error(None)


@pytest.mark.timeout_seconds(10)
def test_detached_head_descendant_with_dirty_work_reattaches_and_preserves_work(
    tmp_path: Path,
) -> None:
    """AC10: Descendant detached HEAD + dirty work reattaches via ancestry/CAS and preserves dirty work."""
    repo = _make_test_repo(tmp_path, "feature/work")
    base_sha = repo.head.commit.hexsha

    # Detach HEAD at base commit, then make a commit while detached
    repo.git.checkout(base_sha)
    assert repo.head.is_detached
    actor = Actor("Test Committer", "test@example.com")
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("detached commit content\n", encoding="utf-8")
    repo.index.add(["feature.txt"])
    repo.index.commit("commit made while detached", author=actor, committer=actor)
    detached_sha = repo.head.commit.hexsha
    assert detached_sha != base_sha

    # Add unstaged tracked changes (dirty work)
    feature_file.write_text("unstaged dirty changes\n", encoding="utf-8")
    assert repo.is_dirty(untracked_files=False)

    message_file = _make_commit_artifact(tmp_path, "feat: incorporate dirty work")

    # Execute commit effect
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="feature/work",
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached, "HEAD must be reattached to branch"
    assert repo.active_branch.name == "feature/work"
    assert repo.head.commit.parents[0].hexsha == detached_sha
    assert repo.head.commit.message.splitlines()[0] == "feat: incorporate dirty work"
    assert (tmp_path / "feature.txt").read_text(encoding="utf-8") == "unstaged dirty changes\n"


@pytest.mark.timeout_seconds(10)
def test_detached_head_equals_integration_target_skips_integration_target(
    tmp_path: Path,
) -> None:
    """AC10: Exact-tip reattach skips the integration target to avoid clobbering it."""
    from ralph.config.models import UnifiedConfig

    repo = _make_test_repo(tmp_path, "feature/work")
    main_sha = repo.heads.main.commit.hexsha

    # Detach HEAD exactly at main's commit tip
    repo.git.checkout(main_sha)
    assert repo.head.is_detached

    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})

    # Test via execute_commit_effect: when HEAD is detached at main tip,
    # reattach does NOT attach to or clobber 'main'
    work_file = tmp_path / "feature.txt"
    work_file.write_text("feature content on target tip\n", encoding="utf-8")
    message_file = _make_commit_artifact(tmp_path, "feat: work on top of main tip")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        config=config,
        feature_branch="feature/work",
    )
    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert repo.active_branch.name != "main", "Must not reattach directly to integration target"
    assert repo.heads.main.commit.hexsha == main_sha, "Integration target tip must not be clobbered"


@pytest.mark.timeout_seconds(10)
def test_detached_head_equals_or_descendant_of_occupied_worktree_skips_occupied(
    tmp_path: Path,
) -> None:
    """AC10: Reattach skips branches checked out in other worktrees."""
    repo = _make_test_repo(tmp_path, "feature/work")
    actor = Actor("Test Committer", "test@example.com")

    # Create another branch 'branch-in-other-wt' from feature/work
    repo.git.branch("branch-in-other-wt", "feature/work")
    other_sha = repo.heads["branch-in-other-wt"].commit.hexsha

    # Setup a second worktree with 'branch-in-other-wt' checked out
    wt_dir = tmp_path.parent / (tmp_path.name + "-wt2")
    repo.git.worktree("add", str(wt_dir), "branch-in-other-wt")

    try:
        # Return to tmp_path and detach HEAD at other_sha (equal to occupied branch tip)
        repo.git.checkout(other_sha)
        assert repo.head.is_detached

        # Exact-tip resolution must skip the occupied branch
        work_file1 = tmp_path / "exact_tip.txt"
        work_file1.write_text("exact tip work\n", encoding="utf-8")
        message_file1 = _make_commit_artifact(tmp_path, "feat: work on exact tip")
        event1 = execute_commit_effect(
            CommitEffect(message_file=str(message_file1)),
            tmp_path,
            branch="branch-in-other-wt",
            feature_branch="feature/work",
        )
        assert event1 == PipelineEvent.COMMIT_SUCCESS
        repo = Repo(tmp_path)
        assert not repo.head.is_detached
        assert repo.active_branch.name != "branch-in-other-wt", (
            "Exact-tip must skip occupied worktree branch"
        )
        assert repo.heads["branch-in-other-wt"].commit.hexsha == other_sha

        # Also test descendant case: make a commit on detached HEAD
        repo = Repo(tmp_path)
        repo.git.checkout(other_sha)
        assert repo.head.is_detached
        descendant_file = tmp_path / "descendant.txt"
        descendant_file.write_text("descendant commit\n", encoding="utf-8")
        repo.index.add(["descendant.txt"])
        repo.index.commit("descendant on detached HEAD", author=actor, committer=actor)
        descendant_sha = repo.head.commit.hexsha
        repo.git.checkout(descendant_sha)
        assert repo.head.is_detached

        # Reattach must skip the occupied branch for both exact-tip and fast-forward
        work_file2 = tmp_path / "descendant_work.txt"
        work_file2.write_text("descendant work\n", encoding="utf-8")
        message_file2 = _make_commit_artifact(tmp_path, "feat: descendant work")
        event2 = execute_commit_effect(
            CommitEffect(message_file=str(message_file2)),
            tmp_path,
            branch="branch-in-other-wt",
            feature_branch="feature/work",
        )
        assert event2 == PipelineEvent.COMMIT_SUCCESS
        repo = Repo(tmp_path)
        assert not repo.head.is_detached
        assert repo.active_branch.name != "branch-in-other-wt", (
            "Must not reattach to occupied branch"
        )
        # The occupied branch tip in repo must NOT have moved to descendant_sha
        assert repo.heads["branch-in-other-wt"].commit.hexsha == other_sha
    finally:
        # Cleanup worktree
        repo.git.worktree("remove", "--force", str(wt_dir))


def test_collision_handling_at_divergent_sha_uses_collision_safe_name(
    tmp_path: Path,
) -> None:
    """AC10: When candidate branch exists at a divergent SHA, fresh branch uses collision-safe name and does not overwrite."""
    from ralph.pipeline._commit_reattach_fallback import create_fresh_branch_for_detached

    def _local_branch_exists(root: Path, branch_name: str) -> bool:
        return branch_name in Repo(root).heads

    repo = _make_test_repo(tmp_path, "feature/work")
    existing_sha = repo.heads["feature/work"].commit.hexsha

    # Create a divergent commit on detached HEAD (branched from initial commit 'main')
    repo.git.checkout("main")
    actor = Actor("Test Committer", "test@example.com")
    divergent_file = tmp_path / "divergent.txt"
    divergent_file.write_text("divergent branch work\n", encoding="utf-8")
    repo.index.add(["divergent.txt"])
    repo.index.commit("divergent commit", author=actor, committer=actor)
    divergent_sha = repo.head.commit.hexsha
    repo.git.checkout(divergent_sha)
    assert repo.head.is_detached

    # Preferred candidate is 'feature/work', which exists at existing_sha
    short_sha = divergent_sha[:7]
    expected_fresh_name = f"feature/work-{short_sha}"

    chosen = create_fresh_branch_for_detached(
        tmp_path,
        detached_head_sha=divergent_sha,
        candidates=iter(["feature/work"]),
        branch_exists=_local_branch_exists,
        integration_target=None,
        occupied=frozenset(),
    )

    assert chosen == expected_fresh_name
    repo = Repo(tmp_path)
    # 1. Existing branch was NOT overwritten
    assert repo.heads["feature/work"].commit.hexsha == existing_sha
    # 2. Fresh branch exists at divergent_sha
    assert repo.heads[expected_fresh_name].commit.hexsha == divergent_sha

    # 3. Test second collision: if feature/work-<short_sha> also already exists,
    # it must fall back to wt-<short_sha> without overwriting
    second_chosen = create_fresh_branch_for_detached(
        tmp_path,
        detached_head_sha=divergent_sha,
        candidates=iter(["feature/work"]),
        branch_exists=_local_branch_exists,
        integration_target=None,
        occupied=frozenset(),
    )
    expected_second_name = f"wt-{short_sha}"
    assert second_chosen == expected_second_name
    assert repo.heads["feature/work"].commit.hexsha == existing_sha
    assert repo.heads[expected_fresh_name].commit.hexsha == divergent_sha
    assert repo.heads[expected_second_name].commit.hexsha == divergent_sha


@pytest.mark.timeout_seconds(10)
def test_committed_feature_work_lands_on_configured_integration_target(
    tmp_path: Path,
) -> None:
    """AC10/Criterion 10: Verify committed feature work from detached HEAD lands on the configured integration target."""
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    repo = _make_test_repo(tmp_path, "feature/work")
    initial_main_sha = repo.heads.main.commit.hexsha

    # Detach HEAD at feature/work commit
    feature_sha = repo.head.commit.hexsha
    repo.git.checkout(feature_sha)
    assert repo.head.is_detached

    # Add feature work while detached
    work_file = tmp_path / "feature.txt"
    work_file.write_text("feature content to be integrated\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: implement target integration work")
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})

    # Execute commit effect on detached HEAD
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        config=config,
    )
    assert event == PipelineEvent.COMMIT_SUCCESS

    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature/work"
    feature_commit_sha = repo.head.commit.hexsha
    assert feature_commit_sha != initial_main_sha

    # Drive auto_integrate_after_commit to integrate feature work into 'main'
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_path),
        RebaseState(),
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )
    assert outcome is not None
    assert outcome.fast_forwarded

    repo = Repo(tmp_path)
    # The integration target 'main' must now include the committed feature work
    main_tip_sha = repo.heads.main.commit.hexsha
    assert main_tip_sha == feature_commit_sha, (
        "Feature commit must land on configured integration target 'main'"
    )
    assert (tmp_path / "feature.txt").read_text(
        encoding="utf-8"
    ) == "feature content to be integrated\n"


@pytest.mark.timeout_seconds(10)
def test_execute_commit_effect_from_deps_detached_dirty_commits_on_feature_branch_not_target(
    tmp_path: Path,
) -> None:
    """WU-3: _execute_commit_effect_from_deps preserves target branch and commits to feature branch."""
    from rich.console import Console

    from ralph.config.enums import Verbosity
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import make_display_context
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.runner import execute_commit_effect_from_deps
    from ralph.workspace.scope import WorkspaceScope

    repo = _make_test_repo(tmp_path, "feature/work")
    repo.git.branch("-m", "main", "release")
    initial_release_sha = repo.heads.release.commit.hexsha

    # Detach HEAD at feature commit
    feature_sha = repo.heads["feature/work"].commit.hexsha
    repo.git.checkout(feature_sha)
    assert repo.head.is_detached

    # Add dirty work while detached
    work_file = tmp_path / "feature.txt"
    work_file.write_text("feature uncommitted changes\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: implement work from runner deps")
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "release"}})

    display_context = make_display_context(
        console=Console(record=True, force_terminal=False, width=120, color_system=None),
        force_width=120,
    )
    event = execute_commit_effect_from_deps(
        CommitEffect(message_file=str(message_file)),
        PipelineDeps(display_context=display_context),
        WorkspaceScope(tmp_path),
        None,
        Verbosity.VERBOSE,
        config=config,
    )
    assert event == PipelineEvent.COMMIT_SUCCESS

    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature/work"
    new_feature_sha = repo.head.commit.hexsha

    # The configured integration target 'release' MUST NOT be advanced directly
    assert repo.heads.release.commit.hexsha == initial_release_sha
    assert new_feature_sha != initial_release_sha
    assert (tmp_path / "feature.txt").read_text(encoding="utf-8") == "feature uncommitted changes\n"


@pytest.mark.timeout_seconds(10)
def test_auto_integrate_phase_boundary_reattaches_detached_head_preserving_staged_and_dirty_work(
    tmp_path: Path,
) -> None:
    """WU-4: auto-integrate phase transition reattaches detached HEAD and preserves staged/dirty work."""
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_on_phase_transition
    from ralph.pipeline.auto_integrate_boundary import phase_boundary_outcome
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    repo = _make_test_repo(tmp_path, "feature/work")
    feature_sha = repo.heads["feature/work"].commit.hexsha

    # Detach HEAD at feature commit
    repo.git.checkout(feature_sha)
    assert repo.head.is_detached

    # Add both staged and unstaged changes
    staged_file = tmp_path / "staged.txt"
    staged_file.write_text("staged content\n", encoding="utf-8")
    repo.index.add(["staged.txt"])

    dirty_file = tmp_path / "feature.txt"
    dirty_file.write_text("dirty unstaged content\n", encoding="utf-8")

    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})

    # Test direct phase_boundary_outcome reattachment
    handled, _outcome = phase_boundary_outcome(
        config, tmp_path, "main", RebaseState(), rebase_stop_resolver=None
    )
    assert handled is True
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature/work"
    assert (tmp_path / "staged.txt").read_text(encoding="utf-8") == "staged content\n"
    assert (tmp_path / "feature.txt").read_text(encoding="utf-8") == "dirty unstaged content\n"
    assert "staged.txt" in [item.a_path for item in repo.index.diff("HEAD")]

    # Detach again and test auto_integrate_on_phase_transition reattachment
    repo = Repo(tmp_path)
    repo.git.checkout(feature_sha)
    assert repo.head.is_detached

    _res = auto_integrate_on_phase_transition(
        config,
        WorkspaceScope(tmp_path),
        RebaseState(),
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature/work"
    assert (tmp_path / "staged.txt").read_text(encoding="utf-8") == "staged content\n"
    assert (tmp_path / "feature.txt").read_text(encoding="utf-8") == "dirty unstaged content\n"


@pytest.mark.timeout_seconds(10)
def test_detached_head_with_staged_changes_commits_on_feature_branch_and_integrates(
    tmp_path: Path,
) -> None:
    """AC10: Worktree on detached HEAD with staged changes commits to feature branch, integrates, and avoids loop."""
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    repo = _make_test_repo(tmp_path, "feature/work")
    feature_sha = repo.heads["feature/work"].commit.hexsha

    # Detach HEAD at the feature commit
    repo.git.checkout(feature_sha)
    assert repo.head.is_detached

    # Add staged changes
    staged_file = tmp_path / "staged_feature.txt"
    staged_file.write_text("staged feature changes\n", encoding="utf-8")
    repo.index.add(["staged_feature.txt"])

    # Also add uncommitted tracked changes
    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified content\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: commit staged and tracked changes")
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        config=config,
    )

    # Must succeed, reattach to feature/work, and commit onto feature branch
    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached, "HEAD must not be detached after commit"
    assert repo.active_branch.name == "feature/work"
    committed_sha = repo.head.commit.hexsha

    # Reducer advances to next phase without failed_terminal
    policy = _load_pipeline_policy()
    state = PipelineState(phase="development_commit", previous_phase="development_commit_cleanup")
    next_state, _ = reduce(state, event, policy)
    assert next_state.phase != "failed_terminal"
    assert next_state.phase in ("development_analysis", "development_final_commit_cleanup")

    # Integrate the committed work into 'main'
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_path),
        RebaseState(),
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )
    assert outcome is not None
    assert outcome.fast_forwarded

    # Verified: work is landed on main, HEAD is on feature branch, no cleanup loop hitting detached HEAD
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.heads.main.commit.hexsha == committed_sha


def test_persistent_repairable_failure_without_recovery_phase_escalates_to_automated_resolver(
    tmp_path: Path,
) -> None:
    """O1 retry risk: Persistent repairable Git failure under policy without cleanup/recovery.

    Verifies that when a persistent repairable Git failure (such as index.lock
    contention or detached HEAD error) occurs under a policy defining neither
    ``commit_recovery`` nor ``commit_cleanup``:
    1. Retries in-place up to the bounded ``_MAX_COMMIT_RECOVERY_ATTEMPTS`` cap.
    2. Once the counter is exhausted, does NOT cycle in-place indefinitely.
    3. Escalates to the policy's automated recovery/resolver route (e.g. upstream
       execution phase ``development``).
    4. Preserves the validated commit message state and message file on disk.
    5. Never reports failed work completed.
    6. Never escalates a repairable Git state to ``failed_terminal``.
    """
    _make_test_repo(tmp_path, "feature/work")
    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified\n", encoding="utf-8")
    message_file = _make_commit_artifact(tmp_path, "feat: black box repairable retry")

    # Construct policy without cleanup/recovery phases
    base_policy = _load_pipeline_policy()
    phases = {
        name: defn
        for name, defn in base_policy.phases.items()
        if defn.role != "commit_cleanup" and "cleanup" not in name and "recovery" not in name
    }
    dev_phase = phases["development"]
    phases["development"] = dev_phase.model_copy(
        update={
            "transitions": dev_phase.transitions.model_copy(
                update={"on_success": "development_commit"}
            )
        }
    )
    policy_no_cleanup = base_policy.model_copy(update={"phases": phases})

    # Ensure policy has no commit_recovery or commit_cleanup phase
    assert not any(
        defn.role == "commit_cleanup" or "cleanup" in name or "recovery" in name
        for name, defn in policy_no_cleanup.phases.items()
    )

    initial_commit = CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True)
    state = PipelineState(
        phase="development_commit",
        previous_phase="development",
        commit=initial_commit,
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
    )

    # Inject persistent repairable Git failure at the real effect/reducer boundary:
    # A persistent index.lock contention error
    effect = CommitEffect(message_file=str(message_file))
    lock_err = "GitOperationError: Unable to create '.git/index.lock': File exists"

    def failing_stage_all(_root: Path | str) -> None:
        raise RuntimeError(lock_err)

    # Attempt 1: in-place retry, epoch becomes 1
    event1 = execute_commit_effect(
        effect,
        tmp_path,
        stage_all_fn=failing_stage_all,
    )
    assert event1 is PipelineEvent.COMMIT_FAILURE
    assert message_file.exists(), "Validated message file must be preserved on disk"

    state1, _ = reduce(state, event1, policy_no_cleanup)
    assert state1.phase == "development_commit"
    assert state1.recovery_epoch == 1
    assert state1.pending_cycle_outcome is None
    assert state1.commit.agent_invoked is True
    assert state1.previous_phase == "development"

    # Attempt 2: in-place retry, epoch becomes 2
    event2 = execute_commit_effect(
        effect,
        tmp_path,
        stage_all_fn=failing_stage_all,
    )
    state2, _ = reduce(state1, event2, policy_no_cleanup)
    assert state2.phase == "development_commit"
    assert state2.recovery_epoch == 2
    assert state2.pending_cycle_outcome is None
    assert state2.commit.agent_invoked is True

    # Attempt 3: in-place retry reaching cap (3)
    event3 = execute_commit_effect(
        effect,
        tmp_path,
        stage_all_fn=failing_stage_all,
    )
    state3, _ = reduce(state2, event3, policy_no_cleanup)
    assert state3.phase == "development_commit"
    assert state3.recovery_epoch == 3
    assert state3.pending_cycle_outcome is None
    assert state3.commit.agent_invoked is True

    # Attempt 4: beyond the bounded counter — must NOT cycle in-place indefinitely,
    # must NOT terminally fail, but must escalate to automated resolver route ('development')
    event4 = execute_commit_effect(
        effect,
        tmp_path,
        stage_all_fn=failing_stage_all,
    )
    escalated_state, _ = reduce(state3, event4, policy_no_cleanup)

    # Demonstrates the livelock was broken and automated recovery was dispatched:
    assert escalated_state.phase != "development_commit", "Must not cycle in-place indefinitely"
    assert escalated_state.phase != "failed_terminal", (
        "Must not terminally fail repairable git state"
    )
    assert escalated_state.phase == "development", "Must escalate to automated resolver phase"
    assert escalated_state.recovery_epoch == 3, "Counter remains bounded at cap"
    assert escalated_state.pending_cycle_outcome is None, "Must never report failed work completed"
    assert escalated_state.last_execution_result_status == "failed"
    assert escalated_state.commit.agent_invoked is True, "Validated commit state must be preserved"
    assert message_file.exists(), "Validated commit message file must remain on disk"


def test_persistent_repairable_failure_escalates_to_workflow_fallback_when_configured() -> None:
    """O1 retry risk: Persistent repairable commit failure honors policy workflow_fallback."""
    from ralph.policy.models._phase_workflow_fallback import PhaseWorkflowFallback

    base_policy = _load_pipeline_policy()
    phases = {
        name: defn
        for name, defn in base_policy.phases.items()
        if defn.role != "commit_cleanup" and "cleanup" not in name and "recovery" not in name
    }
    # Configure workflow_fallback on development_commit to point to planning
    commit_phase = phases["development_commit"].model_copy(
        update={"workflow_fallback": PhaseWorkflowFallback(target="planning")}
    )
    phases["development_commit"] = commit_phase
    policy_with_fallback = base_policy.model_copy(update={"phases": phases})

    state = PipelineState(
        phase="development_commit",
        previous_phase="development",
        recovery_epoch=3,  # exhausted
        pending_cycle_outcome="completed",
        last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
    )
    set_last_commit_error("GitOperationError: Unable to create '.git/index.lock': File exists")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy_with_fallback)
        assert next_state.phase == "planning"
        assert next_state.phase != "failed_terminal"
        assert next_state.pending_cycle_outcome is None
        assert next_state.recovery_epoch == 3
    finally:
        set_last_commit_error(None)
