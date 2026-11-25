from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo
from git.exc import GitCommandError

from ralph.git.git_command_queue import route_git_method
from ralph.pipeline.conflict_resolution.driver import paths_still_unresolved
from ralph.pipeline.conflict_resolution.git_worktree_observer import GitWorktreeConflictObserver

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_ACTOR = Actor("Ralph Test", "test@ralph.internal")


def _commit(repo: Repo, root: Path, message: str) -> None:
    route_git_method(repo, "add", ("-A",))
    route_git_method(repo, "commit", ("-q", "-m", message))


def _delete_modify_stop(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    repo = Repo.init(root, initial_branch="main")
    with repo.config_writer() as config:
        config.set_value("user", "name", _ACTOR.name)
        config.set_value("user", "email", _ACTOR.email)
    (root / "build.rs").write_text("fn main() {}\n", encoding="utf-8")
    (root / "lib.rs").write_text("pub fn a() {}\n", encoding="utf-8")
    _commit(repo, root, "base")
    route_git_method(repo, "checkout", ("-q", "-b", "feature"))
    (root / "build.rs").unlink()
    _commit(repo, root, "feature deletes build.rs")
    route_git_method(repo, "checkout", ("-q", "main"))
    (root / "build.rs").write_text('fn main() { println!("alias"); }\n', encoding="utf-8")
    _commit(repo, root, "main edits build.rs")
    route_git_method(repo, "checkout", ("-q", "feature"))
    with contextlib.suppress(GitCommandError):
        route_git_method(repo, "rebase", ("main",))
    return root


def test_a_delete_decision_on_a_delete_modify_conflict_counts_as_resolved(
    tmp_path: Path,
) -> None:
    root = _delete_modify_stop(tmp_path)
    assert (root / "build.rs").exists(), "git leaves the modified side for the resolver"

    (root / "build.rs").unlink()

    unresolved = paths_still_unresolved(
        root,
        ("build.rs",),
        ("build.rs",),
        present_before=frozenset({"build.rs"}),
        substantive=frozenset({"build.rs"}),
        observer=GitWorktreeConflictObserver(),
    )

    assert unresolved == ()
    status = str(route_git_method(Repo(root), "status", ("--porcelain",)))
    assert status.strip() == "D  build.rs", "the deletion is staged, no longer unmerged"


def test_a_keep_decision_on_a_delete_modify_conflict_counts_as_resolved(
    tmp_path: Path,
) -> None:
    root = _delete_modify_stop(tmp_path)

    unresolved = paths_still_unresolved(
        root,
        ("build.rs",),
        ("build.rs",),
        present_before=frozenset({"build.rs"}),
        substantive=frozenset({"build.rs"}),
        observer=GitWorktreeConflictObserver(),
    )

    assert unresolved == ()


def test_deleting_a_conflicted_file_that_needed_a_merge_is_still_unresolved(
    tmp_path: Path,
) -> None:
    root = _delete_modify_stop(tmp_path)
    (root / "build.rs").unlink()

    unresolved = paths_still_unresolved(
        root,
        ("build.rs",),
        (),
        present_before=frozenset({"build.rs"}),
        substantive=frozenset({"build.rs"}),
        observer=GitWorktreeConflictObserver(),
    )

    assert unresolved == ("build.rs",)
