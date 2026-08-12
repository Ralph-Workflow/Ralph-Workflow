"""Unit tests for the workspace-bounded git cwd validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.tools._git_cwd_validator import resolve_git_cwd
from ralph.mcp.tools.coordination import InvalidParamsError


def _inside_runner(resolved: Path) -> Path:
    """Fake probe: the resolved cwd is itself a repo top-level."""
    return resolved


def test_none_and_empty_resolve_to_workspace_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    assert resolve_git_cwd(
        workspace_root=root, requested_cwd=None, git_runner=_inside_runner
    ) == root.resolve()
    assert resolve_git_cwd(
        workspace_root=root, requested_cwd="", git_runner=_inside_runner
    ) == root.resolve()


def test_relative_path_inside_workspace_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    nested = root / "nested-repo"
    nested.mkdir(parents=True)
    assert resolve_git_cwd(
        workspace_root=root, requested_cwd="nested-repo", git_runner=_inside_runner
    ) == nested.resolve()


def test_dotdot_traversal_outside_workspace_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(InvalidParamsError) as exc_info:
        resolve_git_cwd(
            workspace_root=root, requested_cwd="..", git_runner=_inside_runner
        )
    message = str(exc_info.value)
    assert str((root / "..").resolve()) in message
    assert str(root.resolve()) in message


def test_absolute_path_outside_workspace_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    external = tmp_path / "external_repo"
    external.mkdir()
    with pytest.raises(InvalidParamsError) as exc_info:
        resolve_git_cwd(
            workspace_root=root,
            requested_cwd=str(external),
            git_runner=_inside_runner,
        )
    message = str(exc_info.value)
    assert str(external.resolve()) in message
    assert str(root.resolve()) in message


def test_symlink_bypass_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    external = tmp_path / "external_repo"
    external.mkdir()
    sneaky = root / "sneaky"
    sneaky.symlink_to(external)
    with pytest.raises(InvalidParamsError) as exc_info:
        resolve_git_cwd(
            workspace_root=root, requested_cwd="sneaky", git_runner=_inside_runner
        )
    message = str(exc_info.value)
    assert str(external.resolve()) in message
    assert str(root.resolve()) in message


def test_parent_repo_toplevel_outside_workspace_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "parent_repo" / "subfolder" / "workspace"
    root.mkdir(parents=True)
    parent_top = tmp_path / "parent_repo"

    def outside_runner(_resolved: Path) -> Path:
        return parent_top

    with pytest.raises(InvalidParamsError) as exc_info:
        resolve_git_cwd(
            workspace_root=root, requested_cwd=None, git_runner=outside_runner
        )
    message = str(exc_info.value)
    assert str(parent_top.resolve()) in message
    assert str(root.resolve()) in message


def test_nested_toplevel_inside_workspace_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    nested = root / "nested-repo"
    nested.mkdir(parents=True)

    def nested_runner(_resolved: Path) -> Path:
        return nested

    assert (
        resolve_git_cwd(
            workspace_root=root, requested_cwd="nested-repo", git_runner=nested_runner
        )
        == nested.resolve()
    )


def test_no_repo_found_is_allowed_through(tmp_path: Path) -> None:
    """A cwd with no containing repo is not rejected by the validator.

    The handler's own git invocation surfaces the real failure; the
    validator only guards the workspace boundary.
    """
    root = tmp_path / "workspace"
    plain = root / "plain-dir"
    plain.mkdir(parents=True)

    def no_repo_runner(_resolved: Path) -> Path | None:
        return None

    assert (
        resolve_git_cwd(
            workspace_root=root, requested_cwd="plain-dir", git_runner=no_repo_runner
        )
        == plain.resolve()
    )
