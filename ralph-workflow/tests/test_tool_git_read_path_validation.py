"""Real-Git regression for the workspace-bounded git cwd contract.

Pins the two bypass shapes the MCP git read boundary must refuse:

1. A symlink inside the workspace pointing at an external repository.
2. A workspace that is itself a plain subdirectory of an unrelated
   parent repository (git discovers the parent's top-level).

Both must raise ``InvalidParamsError`` naming the offending path and
the workspace root; the framework boundary converts that into a
``ToolResult(is_error=True)`` for the wire response (covered by the
existing MCP framework tests). The legacy paths (``cwd=None`` and
``cwd="."``) must keep working against a real repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.mcp.tools.git_read import handle_git_status

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


class _Session:
    """Session stub approving every capability the handlers gate on."""

    def check_capability(self, capability: str) -> str:
        return "approved"


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root


def _init_repo_with_commit(path: Path) -> Repo:
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(path)
    writer = repo.config_writer()
    try:
        writer.set_value("user", "name", "Test User")
        writer.set_value("user", "email", "test@example.com")
    finally:
        writer.release()
    readme = path / "README.md"
    readme.write_text("seed", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit")
    return repo


def test_symlink_to_external_repo_is_refused(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_repo = _init_repo_with_commit(workspace_root)
    try:
        external_repo = _init_repo_with_commit(tmp_path / "external_repo")
        try:
            sneaky = workspace_root / "sneaky"
            sneaky.symlink_to(tmp_path / "external_repo")
            with pytest.raises(InvalidParamsError) as exc_info:
                handle_git_status(_Session(), _Workspace(workspace_root), {"cwd": "sneaky"})
            message = str(exc_info.value)
            assert str((tmp_path / "external_repo").resolve()) in message
            assert str(workspace_root.resolve()) in message
        finally:
            external_repo.close()
    finally:
        workspace_repo.close()


def test_parent_repo_toplevel_bypass_is_refused(tmp_path: Path) -> None:
    """A workspace inside an unrelated parent repo must be refused.

    The resolved cwd is inside the workspace, but git's discovered
    top-level is the parent repository outside the workspace — the
    two-dimensional check exists precisely for this shape.
    """
    parent_repo = _init_repo_with_commit(tmp_path / "parent_repo")
    try:
        workspace_root = tmp_path / "parent_repo" / "subfolder" / "workspace"
        workspace_root.mkdir(parents=True)
        with pytest.raises(InvalidParamsError) as exc_info:
            handle_git_status(_Session(), _Workspace(workspace_root), {"cwd": "."})
        message = str(exc_info.value)
        assert str((tmp_path / "parent_repo").resolve()) in message
        assert str(workspace_root.resolve()) in message
    finally:
        parent_repo.close()


def test_workspace_root_and_dot_cwd_still_allowed(tmp_path: Path) -> None:
    """No regression on the legacy paths against a real repository."""
    workspace_repo = _init_repo_with_commit(tmp_path / "workspace")
    try:
        workspace = _Workspace(tmp_path / "workspace")
        session = _Session()
        result_none = handle_git_status(session, workspace, {})
        assert result_none.is_error is False
        result_dot = handle_git_status(session, workspace, {"cwd": "."})
        assert result_dot.is_error is False
    finally:
        workspace_repo.close()


def test_nested_repo_inside_workspace_is_allowed(tmp_path: Path) -> None:
    """The feature: a nested repository contained in the workspace works."""
    workspace_repo = _init_repo_with_commit(tmp_path / "workspace")
    try:
        nested_repo = _init_repo_with_commit(tmp_path / "workspace" / "nested-repo")
        try:
            result = handle_git_status(
                _Session(), _Workspace(tmp_path / "workspace"), {"cwd": "nested-repo"}
            )
            assert result.is_error is False
        finally:
            nested_repo.close()
    finally:
        workspace_repo.close()
