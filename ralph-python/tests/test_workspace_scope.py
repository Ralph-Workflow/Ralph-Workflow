"""Tests for canonical workspace scope resolution."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from git import Repo

from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path


def test_resolve_workspace_scope_keeps_root_worktree_authority_local(tmp_path: Path) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    repo = Repo.init(main_repo)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    readme = main_repo / "README.md"
    readme.write_text("main", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit")

    child_worktree = tmp_path / "feature-worktree"
    subprocess.run(
        ["git", "worktree", "add", str(child_worktree)],
        cwd=main_repo,
        check=True,
        capture_output=True,
        text=True,
    )

    scope = resolve_workspace_scope(main_repo)

    assert scope.root == main_repo.resolve()
    assert scope.allowed_roots == (main_repo.resolve(),)
