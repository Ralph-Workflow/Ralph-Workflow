from __future__ import annotations

import shutil
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from ralph.pipeline._commit_reattach_fallback import (
    create_fresh_branch_for_detached,
    fast_forward_reattach,
    occupied_worktree_branches,
)
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

_COPY_IGNORE = shutil.ignore_patterns("hooks", "*.sample", "description", "branches")


@pytest.fixture(autouse=True)
def _without_unobserved_commit_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.prompts import commit_evidence

    monkeypatch.setattr(commit_evidence, "commit_generation_diff", lambda _root: "")
    monkeypatch.setattr(commit_evidence, "list_changed_paths", lambda _root: [])


@pytest.fixture(scope="module", autouse=True)
def _init_repo_template(tmp_path_factory: pytest.TempPathFactory) -> None:
    td = tmp_path_factory.mktemp("dhead_template")
    repo = Repo.init(td, initial_branch="main")
    writer = repo.config_writer()
    writer.set_value("user", "name", "Test Committer")
    writer.set_value("user", "email", "test@example.com")
    writer.set_value("gc", "auto", "0")
    writer.set_value("core", "fsync", "none")
    writer.set_value("core", "logAllRefUpdates", "false")
    writer.set_value("commit", "gpgSign", "false")
    writer.release()
    actor = Actor("Test Committer", "test@example.com")

    base_file = td / "README.md"
    base_file.write_text("# Test Repo\n", encoding="utf-8")
    gitignore_file = td / ".gitignore"
    gitignore_file.write_text(".agent/\n", encoding="utf-8")
    repo.index.add(["README.md", ".gitignore"])
    repo.index.commit("initial commit", author=actor, committer=actor)

    repo.git.checkout("-b", "feature/work")
    feature_file = td / "feature.txt"
    feature_file.write_text("feature base\n", encoding="utf-8")
    repo.index.add(["feature.txt"])
    repo.index.commit("feature base commit", author=actor, committer=actor)
    repo.close()

    _MODULE_REPO_TEMPLATE.append(td)


def _detach_head_at(repo: Repo, sha: str) -> None:
    head_file = Path(repo.git_dir) / "HEAD"
    head_file.write_text(f"{sha}\n", encoding="utf-8")


def _make_test_repo(repo_root: Path, feature_branch: str = "feature/work") -> Repo:
    assert _MODULE_REPO_TEMPLATE, (
        "_make_test_repo depends on the autouse module-scoped fixture "
        "_init_repo_template populating _MODULE_REPO_TEMPLATE; the fixture "
        "must run before any test in this module calls _make_test_repo."
    )
    repo_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_MODULE_REPO_TEMPLATE[0], repo_root, dirs_exist_ok=True, ignore=_COPY_IGNORE)
    repo = Repo(repo_root)
    if feature_branch != "feature/work":
        repo.git.checkout("-B", feature_branch)
    return repo


def _make_commit_artifact(repo_root: Path, message: str) -> Path:
    artifact_path = repo_root / ".agent" / "artifacts" / "commit_message.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        f"---\ntype: commit\nsubject: {message}\n---\n",
        encoding="utf-8",
    )
    return artifact_path


@cache
def _load_pipeline_policy() -> PipelinePolicy:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir).pipeline


def test_worktree_on_detached_head_commits_to_feature_branch_without_failed_terminal(
    tmp_path: Path,
) -> None:
    repo = _make_test_repo(tmp_path, "feature/work")
    feature_commit_sha = repo.head.commit.hexsha

    _detach_head_at(repo, feature_commit_sha)
    assert repo.head.is_detached

    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: implement feature work")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="feature/work",
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature/work"
    assert repo.head.commit.message.splitlines()[0] == "feat: implement feature work"

    policy = _load_pipeline_policy()
    state = PipelineState(phase="development_commit", previous_phase="development_commit_review")
    next_state, _ = reduce(state, event, policy)

    assert next_state.phase != "failed_terminal"
    assert next_state.phase in ("development_analysis", "development_final_commit_review")


def test_worktree_on_detached_head_reattaches_without_explicit_branch_arg(
    tmp_path: Path,
) -> None:
    from ralph.config.models import UnifiedConfig

    repo = _make_test_repo(tmp_path, "feature/work")
    feature_commit_sha = repo.head.commit.hexsha

    _detach_head_at(repo, feature_commit_sha)
    assert repo.head.is_detached

    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: production seam")

    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        config=config,
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached, "HEAD must be reattached"
    assert repo.active_branch.name == "feature/work"
    assert repo.head.commit.parents[0].hexsha == feature_commit_sha
    assert repo.head.commit.message.splitlines()[0] == "feat: production seam"


def test_commit_failure_routes_to_automated_recovery_not_failed_terminal() -> None:
    policy = _load_pipeline_policy()
    state = PipelineState(
        phase="development_commit",
        previous_phase="development_commit_review",
        last_error="GitOperationError: index.lock contention",
        pending_cycle_outcome="completed",
    )
    set_last_commit_error("GitOperationError: index.lock contention")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        assert next_state.phase == "development_commit_review"
        assert next_state.phase != "failed_terminal"
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

        assert next_state.pending_cycle_outcome is None
        assert next_state.phase != "failed_terminal"
    finally:
        set_last_commit_error(None)


def test_exhausted_commit_failure_stops_at_terminal_failure() -> None:
    cap = 3
    policy = _load_pipeline_policy()
    state = PipelineState(
        phase="development_commit",
        recovery_epoch=cap,
        pending_cycle_outcome="completed",
        last_error="repeated commit failure",
    )
    set_last_commit_error("repeated commit failure")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        assert next_state.phase == policy.recovery.failed_route
        assert next_state.pending_cycle_outcome is None
        assert next_state.recovery_epoch == cap
        assert next_state.last_execution_result_status == "failed"
    finally:
        set_last_commit_error(None)


@pytest.mark.timeout_seconds(10)
def test_detached_head_descendant_with_dirty_work_reattaches_and_preserves_work(
    tmp_path: Path,
) -> None:
    repo = _make_test_repo(tmp_path, "feature/work")
    base_sha = repo.head.commit.hexsha

    repo.git.checkout(base_sha)
    assert repo.head.is_detached
    actor = Actor("Test Committer", "test@example.com")
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("detached commit content\n", encoding="utf-8")
    repo.index.add(["feature.txt"])
    repo.index.commit("commit made while detached", author=actor, committer=actor)
    detached_sha = repo.head.commit.hexsha
    assert detached_sha != base_sha

    feature_file.write_text("unstaged dirty changes\n", encoding="utf-8")
    assert repo.is_dirty(untracked_files=False)

    message_file = _make_commit_artifact(tmp_path, "feat: incorporate dirty work")

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
    from ralph.config.models import UnifiedConfig

    repo = _make_test_repo(tmp_path, "feature/work")
    main_sha = repo.heads.main.commit.hexsha

    _detach_head_at(repo, main_sha)
    assert repo.head.is_detached

    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})

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
    repo = _make_test_repo(tmp_path, "feature/work")
    actor = Actor("Test Committer", "test@example.com")

    repo.git.branch("branch-in-other-wt", "feature/work")
    other_sha = repo.heads["branch-in-other-wt"].commit.hexsha

    wt_dir = tmp_path.parent / (tmp_path.name + "-wt2")
    repo.git.worktree("add", str(wt_dir), "branch-in-other-wt")

    try:
        _detach_head_at(repo, other_sha)
        assert repo.head.is_detached

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

        repo = Repo(tmp_path)
        _detach_head_at(repo, other_sha)
        assert repo.head.is_detached
        descendant_file = tmp_path / "descendant.txt"
        descendant_file.write_text("descendant commit\n", encoding="utf-8")
        repo.index.add(["descendant.txt"])
        repo.index.commit("descendant on detached HEAD", author=actor, committer=actor)
        descendant_sha = repo.head.commit.hexsha
        _detach_head_at(repo, descendant_sha)
        assert repo.head.is_detached

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
        assert repo.heads["branch-in-other-wt"].commit.hexsha == other_sha
    finally:
        repo.git.worktree("remove", "--force", str(wt_dir))


def test_collision_handling_at_divergent_sha_uses_collision_safe_name(
    tmp_path: Path,
) -> None:

    def _local_branch_exists(root: Path, branch_name: str) -> bool:
        return branch_name in Repo(root).heads

    repo = _make_test_repo(tmp_path, "feature/work")
    existing_sha = repo.heads["feature/work"].commit.hexsha

    repo.git.checkout("main")
    actor = Actor("Test Committer", "test@example.com")
    divergent_file = tmp_path / "divergent.txt"
    divergent_file.write_text("divergent branch work\n", encoding="utf-8")
    repo.index.add(["divergent.txt"])
    repo.index.commit("divergent commit", author=actor, committer=actor)
    divergent_sha = repo.head.commit.hexsha
    _detach_head_at(repo, divergent_sha)
    assert repo.head.is_detached

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
    assert repo.heads["feature/work"].commit.hexsha == existing_sha
    assert repo.heads[expected_fresh_name].commit.hexsha == divergent_sha

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


def test_failed_worktree_query_blocks_fast_forward_of_possibly_occupied_branch(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo = _make_test_repo(repo_root, "feature/work")
    feature_sha = repo.heads["feature/work"].commit.hexsha
    actor = Actor("Test Committer", "test@example.com")
    _detach_head_at(repo, feature_sha)
    (repo_root / "descendant.txt").write_text("descendant\n", encoding="utf-8")
    repo.index.add(["descendant.txt"])
    repo.index.commit("descendant on detached HEAD", author=actor, committer=actor)
    descendant_sha = repo.head.commit.hexsha

    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    occupancy = occupied_worktree_branches(not_a_repo)

    assert occupancy.query_failed
    assert "feature/work" in occupancy
    moved = fast_forward_reattach(
        repo_root,
        detached_head_sha=descendant_sha,
        candidates=iter(["feature/work"]),
        branch_exists=lambda root, name: name in Repo(root).heads,
        integration_target=None,
        occupied=occupancy,
    )

    assert moved is False
    assert Repo(repo_root).heads["feature/work"].commit.hexsha == feature_sha


@pytest.mark.timeout_seconds(10)
def test_committed_feature_work_lands_on_configured_integration_target(
    tmp_path: Path,
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    repo = _make_test_repo(tmp_path, "feature/work")
    initial_main_sha = repo.heads.main.commit.hexsha

    feature_sha = repo.head.commit.hexsha
    _detach_head_at(repo, feature_sha)
    assert repo.head.is_detached

    work_file = tmp_path / "feature.txt"
    work_file.write_text("feature content to be integrated\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: implement target integration work")
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})

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

    feature_sha = repo.heads["feature/work"].commit.hexsha
    _detach_head_at(repo, feature_sha)
    assert repo.head.is_detached

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

    assert repo.heads.release.commit.hexsha == initial_release_sha
    assert new_feature_sha != initial_release_sha
    assert (tmp_path / "feature.txt").read_text(encoding="utf-8") == "feature uncommitted changes\n"


@pytest.mark.timeout_seconds(10)
def test_auto_integrate_phase_boundary_reattaches_detached_head_preserving_staged_and_dirty_work(
    tmp_path: Path,
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_on_phase_transition
    from ralph.pipeline.auto_integrate_boundary import phase_boundary_outcome
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    repo = _make_test_repo(tmp_path, "feature/work")
    feature_sha = repo.heads["feature/work"].commit.hexsha

    _detach_head_at(repo, feature_sha)
    assert repo.head.is_detached

    staged_file = tmp_path / "staged.txt"
    staged_file.write_text("staged content\n", encoding="utf-8")
    repo.index.add(["staged.txt"])

    dirty_file = tmp_path / "feature.txt"
    dirty_file.write_text("dirty unstaged content\n", encoding="utf-8")

    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})

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

    repo = Repo(tmp_path)
    _detach_head_at(repo, feature_sha)
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
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    repo = _make_test_repo(tmp_path, "feature/work")
    feature_sha = repo.heads["feature/work"].commit.hexsha

    _detach_head_at(repo, feature_sha)
    assert repo.head.is_detached

    staged_file = tmp_path / "staged_feature.txt"
    staged_file.write_text("staged feature changes\n", encoding="utf-8")
    repo.index.add(["staged_feature.txt"])

    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified content\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: commit staged and tracked changes")
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        config=config,
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached, "HEAD must not be detached after commit"
    assert repo.active_branch.name == "feature/work"
    committed_sha = repo.head.commit.hexsha

    policy = _load_pipeline_policy()
    state = PipelineState(phase="development_commit", previous_phase="development_commit_review")
    next_state, _ = reduce(state, event, policy)
    assert next_state.phase != "failed_terminal"
    assert next_state.phase in ("development_analysis", "development_final_commit_review")

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
    assert not repo.head.is_detached
    assert repo.heads.main.commit.hexsha == committed_sha


def test_persistent_repairable_failure_without_recovery_phase_stops_at_retry_cap(
    tmp_path: Path,
) -> None:
    _make_test_repo(tmp_path, "feature/work")
    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified\n", encoding="utf-8")
    message_file = _make_commit_artifact(tmp_path, "feat: black box repairable retry")

    base_policy = _load_pipeline_policy()
    phases = {
        name: defn
        for name, defn in base_policy.phases.items()
        if defn.role != "commit_review" and "cleanup" not in name and "recovery" not in name
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

    assert not any(
        defn.role == "commit_review" or "cleanup" in name or "recovery" in name
        for name, defn in policy_no_cleanup.phases.items()
    )

    initial_commit = CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True)
    state = PipelineState(
        phase="development_commit",
        previous_phase="development",
        commit=initial_commit,
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: unmerged paths remain in the index",
    )

    effect = CommitEffect(message_file=str(message_file))
    lock_err = "GitOperationError: unmerged paths remain in the index"

    def failing_stage_all(_root: Path | str) -> None:
        raise RuntimeError(lock_err)

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

    event4 = execute_commit_effect(
        effect,
        tmp_path,
        stage_all_fn=failing_stage_all,
    )
    terminal_state, _ = reduce(state3, event4, policy_no_cleanup)

    assert terminal_state.phase == policy_no_cleanup.recovery.failed_route
    assert terminal_state.recovery_epoch == 3, "Counter remains bounded at cap"
    assert terminal_state.pending_cycle_outcome is None, "Must never report failed work completed"
    assert terminal_state.last_execution_result_status == "failed"
    assert lock_err in (terminal_state.last_error or "")
    assert message_file.exists(), "Validated commit message file must remain on disk"


def test_persistent_repairable_failure_stops_even_with_workflow_fallback() -> None:
    from ralph.policy.models._phase_workflow_fallback import PhaseWorkflowFallback

    base_policy = _load_pipeline_policy()
    phases = {
        name: defn
        for name, defn in base_policy.phases.items()
        if defn.role != "commit_review" and "cleanup" not in name and "recovery" not in name
    }
    commit_phase = phases["development_commit"].model_copy(
        update={"workflow_fallback": PhaseWorkflowFallback(target="planning")}
    )
    phases["development_commit"] = commit_phase
    policy_with_fallback = base_policy.model_copy(update={"phases": phases})

    state = PipelineState(
        phase="development_commit",
        previous_phase="development",
        recovery_epoch=3,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: unmerged paths remain in the index",
    )
    set_last_commit_error("GitOperationError: unmerged paths remain in the index")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy_with_fallback)
        assert next_state.phase == policy_with_fallback.recovery.failed_route
        assert next_state.pending_cycle_outcome is None
        assert next_state.recovery_epoch == 3
    finally:
        set_last_commit_error(None)
