"""Unit tests for workspace-bounded git cwd validation."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.tools._git_cwd_validator import resolve_git_cwd


def _inside_runner(resolved: Path) -> Path:
    return resolved


def test_none_and_empty_resolve_to_workspace_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for requested in (None, ""):
        resolved, is_outside, top_level = resolve_git_cwd(
            workspace_root=root, requested_cwd=requested, git_runner=_inside_runner
        )
        assert (resolved, is_outside, top_level) == (root.resolve(), False, root.resolve())


def test_relative_path_inside_workspace_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    nested = root / "nested-repo"
    nested.mkdir(parents=True)
    assert resolve_git_cwd(
        workspace_root=root, requested_cwd="nested-repo", git_runner=_inside_runner
    ) == (nested.resolve(), False, nested.resolve())


def test_dotdot_traversal_outside_workspace_warns(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    resolved, is_outside, top_level = resolve_git_cwd(
        workspace_root=root, requested_cwd="..", git_runner=_inside_runner
    )
    assert (resolved, is_outside, top_level) == (root.parent.resolve(), True, root.parent.resolve())


def test_absolute_path_outside_workspace_warns(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    external = tmp_path / "external_repo"
    root.mkdir()
    external.mkdir()
    assert resolve_git_cwd(
        workspace_root=root, requested_cwd=str(external), git_runner=_inside_runner
    ) == (external.resolve(), True, external.resolve())


def test_symlink_bypass_warns(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    external = tmp_path / "external_repo"
    root.mkdir()
    external.mkdir()
    (root / "sneaky").symlink_to(external)
    assert resolve_git_cwd(
        workspace_root=root, requested_cwd="sneaky", git_runner=_inside_runner
    ) == (external.resolve(), True, external.resolve())


def test_parent_repo_toplevel_outside_workspace_warns(tmp_path: Path) -> None:
    root = tmp_path / "parent_repo" / "subfolder" / "workspace"
    parent_top = tmp_path / "parent_repo"
    root.mkdir(parents=True)
    resolved, is_outside, top_level = resolve_git_cwd(
        workspace_root=root, requested_cwd=None, git_runner=lambda _path: parent_top
    )
    assert (resolved, is_outside, top_level) == (root.resolve(), True, parent_top.resolve())


def test_no_repo_found_is_allowed_through(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    plain = root / "plain-dir"
    plain.mkdir(parents=True)
    assert resolve_git_cwd(
        workspace_root=root, requested_cwd="plain-dir", git_runner=lambda _path: None
    ) == (plain.resolve(), False, None)
