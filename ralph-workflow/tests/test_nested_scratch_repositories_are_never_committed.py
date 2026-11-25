from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from ralph.git.git_command_queue import route_git_method
from ralph.git.operations import has_uncommitted_changes
from ralph.git.proposal_seal import seal_pending

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_ACTOR = Actor("Ralph Test", "test@ralph.internal")


def _repo(path: Path) -> Repo:
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Ralph Test")
        config.set_value("user", "email", "test@ralph.internal")
    (path / "app.txt").write_text("app\n", encoding="utf-8")
    repo.index.add(["app.txt"])
    repo.index.commit("initial", author=_ACTOR, committer=_ACTOR)
    return repo


def _tree_paths(repo: Repo, tree_sha: str) -> set[str]:
    listing = route_git_method(repo, "ls_tree", ("-r", "--name-only", tree_sha))
    return set(str(listing).splitlines())


def test_a_scratch_repository_inside_the_worktree_is_left_out_of_the_proposal(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "project")
    _repo(tmp_path / "project" / "scratch-worktree")
    (tmp_path / "project" / "feature.txt").write_text("work\n", encoding="utf-8")

    seal = seal_pending(tmp_path / "project", "run", "1-1")

    assert _tree_paths(repo, seal.tree_sha) == {"app.txt", "feature.txt"}


def test_an_already_committed_scratch_gitlink_is_dropped_by_the_next_proposal(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "project")
    _repo(tmp_path / "project" / "scratch-worktree")
    route_git_method(repo, "add", ("-A",))
    route_git_method(repo, "commit", ("-m", "accidentally commit the scratch repository"))
    assert "scratch-worktree" in _tree_paths(repo, "HEAD")

    seal = seal_pending(tmp_path / "project", "run", "1-1")

    assert "scratch-worktree" not in _tree_paths(repo, seal.tree_sha)


def test_work_inside_a_nested_repository_never_makes_the_parent_dirty(tmp_path: Path) -> None:
    _repo(tmp_path / "project")
    nested = _repo(tmp_path / "project" / "scratch-worktree")
    (tmp_path / "project" / "scratch-worktree" / "app.txt").write_text(
        "changed\n", encoding="utf-8"
    )
    assert nested.is_dirty()

    assert has_uncommitted_changes(tmp_path / "project") is False


def test_real_changes_in_the_parent_still_count_as_dirty(tmp_path: Path) -> None:
    _repo(tmp_path / "project")
    _repo(tmp_path / "project" / "scratch-worktree")
    (tmp_path / "project" / "app.txt").write_text("edited\n", encoding="utf-8")

    assert has_uncommitted_changes(tmp_path / "project") is True
