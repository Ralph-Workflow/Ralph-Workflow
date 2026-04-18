"""Tests for canonical workspace scope resolution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    import pytest


def test_resolve_workspace_scope_keeps_root_worktree_authority_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()

    monkeypatch.setattr("ralph.workspace.scope.find_repo_root", lambda _start: main_repo)
    monkeypatch.setattr("ralph.workspace.scope.find_main_worktree_root", lambda _start: main_repo)

    scope = resolve_workspace_scope(main_repo)

    assert scope.root == main_repo.resolve()
    assert scope.allowed_roots == (main_repo.resolve(),)


def test_for_worktree_reroots() -> None:
    worktree_path = Path("/repo/.worktrees/unit-A")
    scope = WorkspaceScope.for_worktree(worktree_path, ("src", "tests/unit"))
    assert scope.root == worktree_path.resolve()


def test_for_worktree_allowed_roots_under_worktree() -> None:
    worktree_path = Path("/repo/.worktrees/unit-A")
    scope = WorkspaceScope.for_worktree(worktree_path, ("src", "tests/unit"))
    resolved_wt = str(worktree_path.resolve())
    for allowed_root in scope.allowed_roots:
        assert str(allowed_root).startswith(resolved_wt)


def test_for_worktree_translates_directories() -> None:
    worktree_path = Path("/repo/.worktrees/unit-B")
    scope = WorkspaceScope.for_worktree(worktree_path, ("src", "docs"))
    # root is always included in allowed_roots by WorkspaceScope.__init__
    expected = {
        worktree_path.resolve(),
        (worktree_path / "src").resolve(),
        (worktree_path / "docs").resolve(),
    }
    assert set(scope.allowed_roots) == expected


def test_for_worktree_original_scope_unmodified() -> None:
    main_root = Path("/repo")
    main_scope = WorkspaceScope(root=main_root, allowed_roots=(main_root / "src",))
    worktree_path = Path("/repo/.worktrees/unit-C")
    worktree_scope = WorkspaceScope.for_worktree(worktree_path, ("src",))
    assert id(main_scope) != id(worktree_scope)
    assert main_scope.root == main_root.resolve()


def test_for_worktree_empty_allowed_dirs() -> None:
    worktree_path = Path("/repo/.worktrees/unit-D")
    scope = WorkspaceScope.for_worktree(worktree_path, ())
    # root is always included; no additional paths
    assert scope.allowed_roots == (worktree_path.resolve(),)
