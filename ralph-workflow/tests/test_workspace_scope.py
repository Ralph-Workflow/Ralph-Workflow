"""Tests for canonical workspace scope resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path


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


def test_for_same_workspace_worker_root_stays_at_repo_root(tmp_path: Path) -> None:
    worker_ns = tmp_path / ".agent" / "workers" / "unit-A"
    worker_ns.mkdir(parents=True)
    scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=tmp_path,
        allowed_directories=("src", "tests/unit"),
        worker_namespace=worker_ns,
    )
    assert scope.root == tmp_path.resolve()


def test_for_same_workspace_worker_allowed_roots_under_repo_root(tmp_path: Path) -> None:
    worker_ns = tmp_path / ".agent" / "workers" / "unit-A"
    worker_ns.mkdir(parents=True)
    scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=tmp_path,
        allowed_directories=("src", "tests/unit"),
        worker_namespace=worker_ns,
    )
    resolved_root = tmp_path.resolve()
    for allowed_root in scope.allowed_roots:
        assert str(allowed_root).startswith(str(resolved_root))


def test_for_same_workspace_worker_does_not_include_repo_root_in_allowed_roots(
    tmp_path: Path,
) -> None:
    """Same-workspace workers must NOT have the repo root in allowed_roots.

    Workers are restricted to their declared edit areas plus their own
    worker namespace. The repo root itself is NOT an allowed root.
    """
    worker_ns = tmp_path / ".agent" / "workers" / "unit-A"
    worker_ns.mkdir(parents=True)
    scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=tmp_path,
        allowed_directories=("src", "tests/unit"),
        worker_namespace=worker_ns,
    )
    # Repo root must NOT be in allowed_roots for same-workspace workers
    assert tmp_path.resolve() not in scope.allowed_roots
    # But the specific directories and worker namespace must be
    assert (tmp_path / "src").resolve() in scope.allowed_roots
    assert (tmp_path / "tests" / "unit").resolve() in scope.allowed_roots
    assert worker_ns.resolve() in scope.allowed_roots


def test_for_same_workspace_worker_translates_directories(tmp_path: Path) -> None:
    worker_ns = tmp_path / ".agent" / "workers" / "unit-B"
    worker_ns.mkdir(parents=True)
    scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=tmp_path,
        allowed_directories=("src", "docs"),
        worker_namespace=worker_ns,
    )
    # Only the specific directories + worker namespace, NOT the repo root
    expected = {
        (tmp_path / "src").resolve(),
        (tmp_path / "docs").resolve(),
        worker_ns.resolve(),
    }
    assert set(scope.allowed_roots) == expected


def test_for_same_workspace_worker_empty_allowed_dirs(tmp_path: Path) -> None:
    """Worker with no allowed directories can only write to its namespace."""
    worker_ns = tmp_path / ".agent" / "workers" / "unit-D"
    worker_ns.mkdir(parents=True)
    scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=tmp_path,
        allowed_directories=(),
        worker_namespace=worker_ns,
    )
    # Only worker_namespace, NOT repo root
    assert tmp_path.resolve() not in scope.allowed_roots
    assert worker_ns.resolve() in scope.allowed_roots
    assert len(scope.allowed_roots) == 1


def test_for_same_workspace_worker_original_scope_unmodified(tmp_path: Path) -> None:
    main_scope = WorkspaceScope(root=tmp_path, allowed_roots=(tmp_path / "src",))
    worker_ns = tmp_path / ".agent" / "workers" / "unit-C"
    worker_ns.mkdir(parents=True)
    worker_scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=tmp_path,
        allowed_directories=("src",),
        worker_namespace=worker_ns,
    )
    assert id(main_scope) != id(worker_scope)
    assert main_scope.root == tmp_path.resolve()


def test_for_same_workspace_worker_rejects_escape_via_dotdot(tmp_path: Path) -> None:
    worker_ns = tmp_path / ".agent" / "workers" / "unit-E"
    worker_ns.mkdir(parents=True)
    with pytest.raises(ValueError, match="escapes"):
        WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("../outside",),
            worker_namespace=worker_ns,
        )


def test_resolve_workspace_scope_prefers_nearest_ralph_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    package_root = repo_root / "ralph-workflow"
    package_root.mkdir(parents=True)
    (package_root / ".agent").mkdir()
    (package_root / ".agent" / "ralph-workflow.toml").write_text("[general]\n")

    monkeypatch.setattr("ralph.workspace.scope.find_repo_root", lambda _start: repo_root)
    monkeypatch.setattr("ralph.workspace.scope.find_main_worktree_root", lambda _start: repo_root)

    scope = resolve_workspace_scope(package_root)

    assert scope.root == package_root.resolve()
    assert scope.local_config_path == (package_root / ".agent" / "ralph-workflow.toml").resolve()
    assert scope.allowed_roots == (package_root.resolve(),)
