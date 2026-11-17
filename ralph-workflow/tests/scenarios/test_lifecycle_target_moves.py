from __future__ import annotations

import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from tests._scenario_harness import AgentMode, FakeAgentRunner, commit_file, paused_text_rebase
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(12),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "paused_rebase_template_reuse": (
        "target-motion cases materialize a cached paused rebase via hardlink copytree"
    ),
    "FakeClock_on_integrate_drives": (
        "target-move integrate drives use FakeClock; reobservation asserts unchanged"
    ),
    "GitPython_ref_and_contains_reads": (
        "tip moves and branch-contains checks use GitPython instead of git subprocess"
    ),
    "feature_seed_via_GitPython": (
        "remote-rejection and sibling cases seed commits through GitPython"
    ),
    "sibling_worktrees_one_mode": (
        "sibling-first and two-worktrees differed only by directory names; one sibling "
        "landing case keeps the shared-goal content-equivalence assert"
    ),
    "target_forward_move_covered_by_midflight": (
        "MERGE: keep target-force-pushed reobservation; a non-force target tip advance "
        "during rebase is covered by test_new_target_commits_during_rebase_are_absorbed"
    ),
    "remote_rejection_covered_by_publication_cleanup": (
        "MERGE: keep sibling landing and force-push reobservation; unreachable-remote "
        "local landing intact is covered by test_local_landing_survives_publication_failure"
    ),
}


def _link_git_objects_or_copy(src: str, dst: str) -> None:
    src_path = Path(src)
    parts = src_path.parts
    if ".git" in parts:
        git_index = parts.index(".git")
        rest = parts[git_index + 1 :]
        if rest and rest[0] == "objects" and len(rest) >= 2:
            try:
                os.link(src, dst)
            except OSError:
                pass
            else:
                return
    shutil.copy2(src, dst)


@functools.lru_cache(maxsize=1)
def _paused_rebase_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-target-paused-"))
    readme = base / "README.md"
    readme.write_text("seed\n", encoding="utf-8")
    repo = Repo.init(base, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgsign", "false")
    repo.index.add(["README.md"])
    repo.index.commit("seed")
    repo.close()
    paused_text_rebase(base)
    return base


def _materialize_paused_rebase(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _paused_rebase_template(),
        dest,
        copy_function=_link_git_objects_or_copy,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )
    return dest


def _sha(root: Path, ref: str) -> str:
    repo = Repo(root)
    try:
        return repo.commit(ref).hexsha
    finally:
        repo.close()


def _contains(root: Path, commit: str, branch: str = "main") -> bool:
    repo = Repo(root)
    try:
        return repo.is_ancestor(commit, branch)
    finally:
        repo.close()


def _write_commit(repo: Repo, relative: str, content: str, message: str) -> str:
    root = Path(repo.working_tree_dir or ".")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relative])
    return repo.index.commit(message).hexsha


def _drive(root: Path, *, resolver: FakeAgentRunner | None = None) -> object:
    clock = FakeClock()
    effective = resolver if resolver is not None else FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    if effective.clock is None:
        effective.clock = clock
    return drive_integration(root, resolver=effective, clock=clock)


def _force_push_target(root: Path) -> str:
    repo = Repo(root)
    try:
        old = repo.commit("main").hexsha
        parent = repo.commit(f"{old}^").hexsha
        tree = repo.commit(parent).tree.hexsha
        moved = repo.git.commit_tree(tree, "-p", parent, "-m", "target moved")
        repo.git.update_ref("refs/heads/main", moved, old)
        return moved
    finally:
        repo.close()


def test_target_force_push_is_reobserved_before_landing(tmp_path: Path) -> None:
    root = _materialize_paused_rebase(tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    moved = _force_push_target(root)

    run = _drive(root, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"target motion must be reobserved: {run.error}"
    assert _contains(root, moved), (
        "landing must be guarded against and recomputed after target motion"
    )
    assert_content_equivalence(
        root,
        contract="the feature must land after target motion",
        feature=feature_sha,
        source_refs=(moved,),
    )


def test_sibling_worktrees_share_one_landing_goal(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _write_commit(repo, "base.txt", "base\n", "base")
        repo.git.checkout("-q", "-b", "feature")
        feature_sha = _write_commit(repo, "shared-change.txt", "same\n", "shared change")
        repo.git.checkout("-q", "main")
        sibling = tmp_git_repo.parent / "sibling-shared"
        repo.git.worktree("add", "-q", "-b", "sibling-shared", str(sibling), "main")
    finally:
        repo.close()
    commit_file(sibling, "shared-change.txt", "same\n", "same shared change")
    sibling_sha = _sha(sibling, "HEAD")
    repo = Repo(tmp_git_repo)
    try:
        repo.git.update_ref("refs/heads/main", sibling_sha)
        repo.git.checkout("-q", "feature")
    finally:
        repo.close()

    run = _drive(tmp_git_repo)

    assert run.error is None, f"sibling landing must be observed as progress: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="concurrent worktrees must converge on one landed result",
        feature=feature_sha,
        source_refs=(sibling_sha,),
    )
