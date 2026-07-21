"""Real-worktree regression coverage for automatic branch integration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha, is_ancestor
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _base_branch(repo_root: Path) -> str:
    return _run(repo_root, "symbolic-ref", "--quiet", "HEAD").stdout.strip().removeprefix(
        "refs/heads/"
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(*, target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}})


def _add_worktree(repo_root: Path, path: Path, branch: str) -> None:
    assert _run(repo_root, "worktree", "add", "-b", branch, str(path)).returncode == 0


def test_commit_rebases_feature_and_fast_forwards_main(tmp_git_repo: Path) -> None:
    """AC-01: a feature worktree rebases and advances the shared main ref."""
    main = _base_branch(tmp_git_repo)
    feature = tmp_git_repo.parent / "feature-a"
    _add_worktree(tmp_git_repo, feature, "feature-a")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        _commit(tmp_git_repo, "main.txt", "main\n", "main change")

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert outcome.last_action in {"rebased", "merged"}
        assert branch_sha(tmp_git_repo, main) == feature_head
        assert is_ancestor(tmp_git_repo, main, feature_head) is True
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_untracked_file_in_feature_worktree_does_not_block_integration(
    tmp_git_repo: Path,
) -> None:
    """AC-01: a non-ignored untracked file must not disable integration.

    A single scratch file left behind by a phase used to trip the rebase
    preconditions and record ``skipped: Working tree is not clean`` at
    every commit seam for the rest of the run.
    """
    main = _base_branch(tmp_git_repo)
    feature = tmp_git_repo.parent / "feature-untracked"
    _add_worktree(tmp_git_repo, feature, "feature-untracked")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        _commit(tmp_git_repo, "main.txt", "main\n", "main change")
        scratch = feature / "scratch.log"
        scratch.write_text("noise\n", encoding="utf-8")

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert outcome.last_action in {"rebased", "merged"}
        assert branch_sha(tmp_git_repo, main) == feature_head
        assert scratch.read_text(encoding="utf-8") == "noise\n"
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_default_config_worktree_agent_advances_shared_main(tmp_git_repo: Path) -> None:
    """AC-01 regression: default target detection lands despite a dirty main checkout."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "tracked.txt", "base\n", "seed tracked file")
    feature = tmp_git_repo.parent / "feature-default"
    _add_worktree(tmp_git_repo, feature, "feature-default")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        dirty_file = tmp_git_repo / "tracked.txt"
        dirty_file.write_text("operator work\n", encoding="utf-8")

        outcome = auto_integrate_after_commit(
            _default_config(), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_head
        assert is_ancestor(tmp_git_repo, main, feature_head) is True
        assert dirty_file.read_text(encoding="utf-8") == "operator work\n"
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_dirty_target_worktree_lands_through_ff_only_and_stays_consistent(
    tmp_git_repo: Path,
) -> None:
    """AC-04: a dirty mainline checkout still lands consistently.

    ``git merge --ff-only`` refuses only for the specific paths it would
    overwrite, so an unrelated uncommitted modification must not push the
    landing onto the ref-only CAS path -- that leaves the checkout's index
    behind and shows the landed commit as a local reverse diff.
    """
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "tracked.txt", "base\n", "seed tracked file")
    feature = tmp_git_repo.parent / "feature-ff"
    _add_worktree(tmp_git_repo, feature, "feature-ff")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        dirty_file = tmp_git_repo / "tracked.txt"
        dirty_file.write_text("operator work\n", encoding="utf-8")

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_head
        # The operator's uncommitted work survives byte-for-byte...
        assert dirty_file.read_text(encoding="utf-8") == "operator work\n"
        # ...and the checkout itself advanced, rather than only the ref.
        assert (tmp_git_repo / "feature.txt").exists()
        status = _run(
            tmp_git_repo, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert "tracked.txt" in status
        assert "feature.txt" not in status
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_two_independent_worktree_agents_converge_on_main(tmp_git_repo: Path) -> None:
    """Regression: an idle feature must rebase after main advances past its tip.

    The former ``no commits beyond target`` skip treated a feature behind main
    as already integrated, so it never received another agent's landed commit.
    """
    main = _base_branch(tmp_git_repo)
    feature_a = tmp_git_repo.parent / "feature-a"
    feature_b = tmp_git_repo.parent / "feature-b"
    _add_worktree(tmp_git_repo, feature_a, "feature-a")
    _add_worktree(tmp_git_repo, feature_b, "feature-b")
    try:
        _commit(feature_a, "a.txt", "a\n", "feature a")
        first = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature_a), RebaseState()
        )
        feature_a_head = _run(feature_a, "rev-parse", "HEAD").stdout.strip()

        assert first is not None
        assert first.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_a_head

        _commit(feature_b, "b.txt", "b\n", "feature b")
        second = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature_b), RebaseState()
        )
        feature_b_head = _run(feature_b, "rev-parse", "HEAD").stdout.strip()

        assert second is not None
        assert second.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_b_head
        assert (feature_b / "a.txt").exists()

        catch_up = auto_integrate_on_phase_transition(
            _build_config(target=main), WorkspaceScope(feature_a), RebaseState()
        )

        assert catch_up is not None
        assert catch_up.fast_forwarded is True
        assert (feature_a / "b.txt").exists()
        assert _run(feature_a, "rev-parse", "HEAD").stdout.strip() == branch_sha(tmp_git_repo, main)
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature_b))
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature_a))


def test_sibling_worktree_landing_mid_integration_is_retried_and_lands(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-08 (linked-worktree topology): a mid-flight sibling landing is absorbed.

    Every agent in this topology shares one ``refs/heads/<main>``, so the
    real race is a sibling advancing it BETWEEN our pre-integration read
    and our fast-forward. The bounded retry must re-integrate onto the
    moved tip rather than discarding the sibling's commit.
    """
    import ralph.pipeline.auto_integrate as _ai_mod

    main = _base_branch(tmp_git_repo)
    feature_a = tmp_git_repo.parent / "feature-a"
    _add_worktree(tmp_git_repo, feature_a, "feature-a")
    try:
        _commit(feature_a, "a.txt", "a\n", "feature a")

        real_ff = _ai_mod._fast_forward_target
        calls: list[int] = []
        sibling_shas: list[str] = []

        def _flaky_ff(
            repo_root: Path, target: str, feature_sha: str
        ) -> tuple[bool, str]:
            calls.append(1)
            if len(calls) == 1:
                # A sibling agent lands on the shared mainline right now.
                sibling_shas.append(
                    _commit(tmp_git_repo, "sibling.txt", "sibling\n", "sibling landing")
                )
                return False, "target advanced concurrently (CAS mismatch)"
            return real_ff(repo_root, target, feature_sha)

        monkeypatch.setattr(_ai_mod, "_fast_forward_target", _flaky_ff)

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature_a), RebaseState()
        )
        feature_a_head = _run(feature_a, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert len(calls) == 2, "a sibling landing must trigger exactly one retry"
        assert outcome.fast_forwarded is True, (
            f"retry must land, got action={outcome.last_action!r}"
            f" reason={outcome.last_reason!r}"
        )
        assert branch_sha(tmp_git_repo, main) == feature_a_head
        # The sibling's work was re-integrated onto, never discarded.
        assert is_ancestor(tmp_git_repo, sibling_shas[0], feature_a_head) is True
        assert (feature_a / "sibling.txt").exists()
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature_a))


def test_rebase_conflict_falls_back_to_endpoint_merge_and_fast_forwards(
    tmp_git_repo: Path,
) -> None:
    """AC-01: a replay conflict can land through a clean endpoint merge."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "one\ntwo\nthree\n", "seed shared")
    feature = tmp_git_repo.parent / "feature-conflict"
    _add_worktree(tmp_git_repo, feature, "feature-conflict")
    try:
        _commit(feature, "shared.txt", "one\nfeature\nthree\n", "feature change")
        _commit(feature, "shared.txt", "one\ntwo\nthree\n", "feature revert")
        _commit(tmp_git_repo, "shared.txt", "one\nmain\nthree\n", "main change")

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.last_action == "merged"
        assert outcome.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_head
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def _strip_conflict_markers(path: Path, merged: str) -> None:
    """Replace a conflicted file's whole content with the resolved text.

    Mirrors what the production resolver agent does: edit files only.
    Ralph -- not the resolver -- stages and commits.
    """
    path.write_text(merged, encoding="utf-8")


def _seed_conflicting_worktrees(
    tmp_git_repo: Path, branch: str
) -> tuple[Path, str, str]:
    """Set up a feature worktree whose change conflicts with a moved main.

    Returns ``(feature_root, main_branch, feature_head_before)``.
    """
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "one\ntwo\nthree\n", "seed shared")
    feature = tmp_git_repo.parent / branch
    _add_worktree(tmp_git_repo, feature, branch)
    feature_head = _commit(
        feature, "shared.txt", "one\nfeature\nthree\n", "feature change"
    )
    _commit(tmp_git_repo, "shared.txt", "one\nmain\nthree\n", "main change")
    # An untracked file also exercises the relaxed rebase preconditions
    # on the conflicted path.
    (feature / "scratch.log").write_text("noise\n", encoding="utf-8")
    return feature, main, feature_head


def test_conflicted_shared_main_resolves_and_fast_forwards_across_worktrees(
    tmp_git_repo: Path,
) -> None:
    """AC-07: conflict -> resolution -> merge commit -> shared-main fast-forward.

    The full path the operator actually hits when two agents edit the
    same lines: the replay conflicts, the endpoint merge conflicts, the
    resolver rewrites the file, Ralph commits deterministically, and the
    shared mainline ref lands on the feature tip.
    """
    feature, main, _ = _seed_conflicting_worktrees(tmp_git_repo, "feature-resolve")
    merged = "one\nfeature+main\nthree\n"

    def _resolver(root: Path, target: str) -> bool:
        assert target == main
        _strip_conflict_markers(root / "shared.txt", merged)
        return True

    try:
        outcome = auto_integrate_after_commit(
            _build_config(target=main),
            WorkspaceScope(feature),
            RebaseState(),
            conflict_resolver=_resolver,
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.last_action == "merged"
        assert outcome.fast_forwarded is True
        assert outcome.last_reason is None
        assert branch_sha(tmp_git_repo, main) == feature_head
        assert is_ancestor(tmp_git_repo, main, feature_head) is True

        resolved = (feature / "shared.txt").read_text(encoding="utf-8")
        assert resolved == merged
        assert "<<<<<<<" not in resolved
        # The merge was COMMITTED, not left in progress.
        assert _run(feature, "rev-parse", "--verify", "MERGE_HEAD").returncode != 0
        assert (feature / "scratch.log").read_text(encoding="utf-8") == "noise\n"
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_failed_resolution_leaves_feature_branch_untouched_across_worktrees(
    tmp_git_repo: Path,
) -> None:
    """AC-07 negative twin: a failed resolution aborts and changes nothing.

    Both branches must be bit-identical to their pre-call state, and no
    merge may be left in progress to poison the next commit seam.
    """
    feature, main, feature_head_before = _seed_conflicting_worktrees(
        tmp_git_repo, "feature-unresolved"
    )
    main_before = branch_sha(tmp_git_repo, main)

    def _resolver(_root: Path, _target: str) -> bool:
        return False

    try:
        outcome = auto_integrate_after_commit(
            _build_config(target=main),
            WorkspaceScope(feature),
            RebaseState(),
            conflict_resolver=_resolver,
        )

        assert outcome is not None
        assert outcome.last_action == "conflict"
        assert outcome.fast_forwarded is False
        assert _run(feature, "rev-parse", "HEAD").stdout.strip() == feature_head_before
        assert branch_sha(tmp_git_repo, main) == main_before
        assert _run(feature, "rev-parse", "--verify", "MERGE_HEAD").returncode != 0
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))
