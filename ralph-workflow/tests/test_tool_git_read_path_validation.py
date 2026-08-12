"""Real-git coverage for git-read cwd validation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination import ToolResult
from ralph.mcp.tools.git_read import (
    handle_git_diff,
    handle_git_log,
    handle_git_show,
    handle_git_status,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


class _Session:
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
    (path / "README.md").write_text("seed", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit")
    return repo


def _assert_warning(
    result: ToolResult, resolved: Path, workspace: Path, top_level: Path | None = None
) -> None:
    assert result.is_error is False
    text = result.content[0].text
    assert text.startswith("WARNING: ")
    assert "outside the workspace" in text
    assert str(resolved.resolve()) in text
    assert str(workspace.resolve()) in text
    if top_level is not None:
        assert f"top_level={top_level.resolve()}" in text


def test_symlink_to_external_repo_warns(tmp_path: Path) -> None:
    workspace_repo = _init_repo_with_commit(tmp_path / "workspace")
    external_repo = _init_repo_with_commit(tmp_path / "external_repo")
    try:
        workspace = tmp_path / "workspace"
        (workspace / "sneaky").symlink_to(tmp_path / "external_repo")
        result = handle_git_status(_Session(), _Workspace(workspace), {"cwd": "sneaky"})
        _assert_warning(result, tmp_path / "external_repo", workspace, tmp_path / "external_repo")
    finally:
        external_repo.close()
        workspace_repo.close()


def test_parent_repo_discovery_appears_in_warning_text(tmp_path: Path) -> None:
    parent_repo = _init_repo_with_commit(tmp_path / "parent_repo")
    try:
        workspace = tmp_path / "parent_repo" / "subfolder" / "workspace"
        workspace.mkdir(parents=True)
        result = handle_git_status(_Session(), _Workspace(workspace), {"cwd": "."})
        _assert_warning(result, workspace, workspace, tmp_path / "parent_repo")
    finally:
        parent_repo.close()


def test_parent_repo_bypass_warns_for_omitted_and_empty_cwd(tmp_path: Path) -> None:
    parent_repo = _init_repo_with_commit(tmp_path / "parent_repo")
    try:
        workspace = tmp_path / "parent_repo" / "subfolder" / "workspace"
        workspace.mkdir(parents=True)
        for params in ({}, {"cwd": ""}):
            result = handle_git_status(_Session(), _Workspace(workspace), params)
            _assert_warning(result, workspace, workspace, tmp_path / "parent_repo")
    finally:
        parent_repo.close()


def test_workspace_root_and_dot_cwd_still_allowed(tmp_path: Path) -> None:
    workspace_repo = _init_repo_with_commit(tmp_path / "workspace")
    try:
        workspace = _Workspace(tmp_path / "workspace")
        for params in ({}, {"cwd": "."}):
            assert handle_git_status(_Session(), workspace, params).is_error is False
    finally:
        workspace_repo.close()


@pytest.mark.parametrize(
    ("handler", "params"),
    [
        (handle_git_status, {"cwd": "nested-repo"}),
        (handle_git_diff, {"cwd": "nested-repo"}),
        (handle_git_log, {"cwd": "nested-repo"}),
        (handle_git_show, {"cwd": "nested-repo", "ref": "HEAD"}),
    ],
)
def test_all_four_handlers_allow_nested_repo_cwd(
    tmp_path: Path,
    handler: Callable[[_Session, _Workspace, dict[str, object]], ToolResult],
    params: dict[str, object],
) -> None:
    workspace_repo = _init_repo_with_commit(tmp_path / "workspace")
    nested_repo = _init_repo_with_commit(tmp_path / "workspace" / "nested-repo")
    try:
        assert handler(_Session(), _Workspace(tmp_path / "workspace"), params).is_error is False
    finally:
        nested_repo.close()
        workspace_repo.close()


@pytest.mark.parametrize(
    ("handler", "params"),
    [
        (handle_git_status, {}),
        (handle_git_diff, {}),
        (handle_git_log, {}),
        (handle_git_show, {"ref": "HEAD"}),
    ],
)
def test_all_four_handlers_warn_on_outside_repo_cwd(
    tmp_path: Path,
    handler: Callable[[_Session, _Workspace, dict[str, object]], object],
    params: dict[str, object],
) -> None:
    workspace_repo = _init_repo_with_commit(tmp_path / "workspace")
    external_repo = _init_repo_with_commit(tmp_path / "external_repo")
    try:
        external = tmp_path / "external_repo"
        result = handler(_Session(), _Workspace(tmp_path / "workspace"), {**params, "cwd": str(external)})
        _assert_warning(result, external, tmp_path / "workspace", external)
    finally:
        external_repo.close()
        workspace_repo.close()
