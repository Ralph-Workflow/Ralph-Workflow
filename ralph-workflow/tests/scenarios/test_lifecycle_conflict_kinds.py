from __future__ import annotations

import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    paused_text_and_gitlink_rebase,
    paused_text_rebase,
)
from tests.scenarios._content_equivalence import (
    assert_content_equivalence,
    drive_integration,
    snapshot_uncommitted,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(12),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "paused_and_gitlink_template_reuse": (
        "resolved-unstaged and gitlink cases materialize cached paused topologies"
    ),
    "FakeClock_on_integrate_drives": (
        "conflict-kind integrate drives use FakeClock; landing asserts unchanged"
    ),
    "GitPython_sha_and_contains_reads": (
        "tip and branch-contains checks use GitPython instead of git subprocess"
    ),
    "conflict_kinds_single_stop": (
        "append-only, delete-modify, and binary conflicts still land in one stop via paired "
        "multi-file commits instead of three serial conflict drives; content-equivalence on "
        "each path unchanged"
    ),
    "dirty_tracked_and_untracked_one_drive": (
        "tracked and untracked dirty work are preserved together in one integrate drive; "
        "both snapshot asserts unchanged versus the prior parametrized pair"
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


def _seed_repo(base: Path) -> None:
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


@functools.lru_cache(maxsize=1)
def _paused_rebase_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-kinds-paused-"))
    _seed_repo(base)
    paused_text_rebase(base)
    return base


@functools.lru_cache(maxsize=1)
def _gitlink_rebase_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-kinds-gitlink-"))
    _seed_repo(base)
    paused_text_and_gitlink_rebase(base)
    return base


def _materialize(template: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        template,
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


def test_resolved_but_unstaged_file_is_finished_without_an_agent(tmp_path: Path) -> None:
    root = _materialize(_paused_rebase_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    (root / "shared.txt").write_text("resolved by operator\n", encoding="utf-8")
    run = _drive(root)
    assert run.resolver_launches == 0, (
        "resolved-but-unstaged content must be staged mechanically before launching an agent"
    )
    assert_content_equivalence(
        root,
        contract="resolved-but-unstaged content must land",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_gitlink_vendor_conflict_uses_a_policy_pointer_and_lands(tmp_path: Path) -> None:
    root = _materialize(_gitlink_rebase_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    run = _drive(root, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))
    assert run.error is None, f"gitlink policy handling must not crash: {run.error}"
    assert_content_equivalence(
        root,
        contract="a gitlink conflict must land with the policy-selected pointer",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_conflict_kinds_land_without_corruption(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        root = Path(repo.working_tree_dir or tmp_git_repo)
        for relative, payload in (
            ("append.dat", "base\n"),
            ("delete.dat", "base\n"),
            ("binary.dat", "base\n"),
        ):
            (root / relative).write_text(payload, encoding="utf-8")
        repo.index.add(["append.dat", "delete.dat", "binary.dat"])
        repo.index.commit("base")
        repo.git.checkout("-q", "-b", "feature")
        (root / "append.dat").write_text("feature append\n", encoding="utf-8")
        (root / "binary.dat").write_text("\x00feature\x00", encoding="utf-8")
        repo.index.remove(["delete.dat"], working_tree=True)
        repo.index.add(["append.dat", "binary.dat"])
        repo.index.commit("feature changes")
        feature_sha = repo.head.commit.hexsha
        repo.git.checkout("-q", "main")
        (root / "append.dat").write_text("target change\n", encoding="utf-8")
        (root / "delete.dat").write_text("target keeps\n", encoding="utf-8")
        (root / "binary.dat").write_text("target change\n", encoding="utf-8")
        repo.index.add(["append.dat", "delete.dat", "binary.dat"])
        repo.index.commit("target changes")
        target_before = repo.head.commit.hexsha
        repo.git.checkout("-q", "feature")
    finally:
        repo.close()
    run = _drive(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))
    assert run.error is None, f"multi-kind conflict handling must not crash: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="append-only, delete-modify, and binary conflicts must land without losing feature content",
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_empty_after_replay_commit_is_preserved_or_explicitly_excluded(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _write_commit(repo, "shared.txt", "same\n", "base")
        repo.git.checkout("-q", "-b", "feature")
        head = repo.head.commit
        empty_sha = repo.git.commit_tree(head.tree.hexsha, "-p", head.hexsha, "-m", "empty replay")
        repo.git.reset("--hard", empty_sha)
    finally:
        repo.close()
    run = _drive(tmp_git_repo)
    reason = "" if run.state is None else (run.state.last_reason or "")
    preserved = _contains(tmp_git_repo, empty_sha)
    assert preserved or "empty" in reason.lower() or "excluded" in reason.lower(), (
        "an empty-after-replay commit must be preserved or explicitly excluded"
    )


def test_dirty_worktree_content_is_preserved_while_feature_lands(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _write_commit(repo, "base.txt", "base\n", "base")
        repo.git.checkout("-q", "-b", "feature")
        feature_sha = _write_commit(repo, "feature.txt", "feature\n", "feature")
    finally:
        repo.close()
    (tmp_git_repo / "base.txt").write_text("uncommitted\n", encoding="utf-8")
    (tmp_git_repo / "untracked.txt").write_text("uncommitted\n", encoding="utf-8")
    preserved = snapshot_uncommitted(tmp_git_repo, ("base.txt", "untracked.txt"))
    run = _drive(tmp_git_repo)
    assert run.error is None, f"dirty tracked and untracked work must not crash: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="dirty tracked and untracked work must survive while feature content lands",
        feature=feature_sha,
        source_refs=("main",),
        preserved=preserved,
    )


def test_failing_hook_does_not_block_landing(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _write_commit(repo, "base.txt", "base\n", "base")
        repo.git.checkout("-q", "-b", "feature")
        feature_sha = _write_commit(repo, "feature.txt", "feature\n", "feature")
    finally:
        repo.close()
    hook = tmp_git_repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    run = _drive(tmp_git_repo)
    assert run.error is None, f"a failing hook must route to a defined autonomous path: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="a failing hook must not prevent committed feature work from landing",
        feature=feature_sha,
        source_refs=("main",),
    )
