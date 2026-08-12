"""Handler-level cwd boundary tests for the git read tools."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.coordination import InvalidParamsError, ToolResult
from ralph.mcp.tools.git_read import (
    handle_git_diff,
    handle_git_log,
    handle_git_show,
    handle_git_status,
)
from ralph.mcp.tools.names import GIT_DIFF_TOOL, GIT_LOG_TOOL, GIT_SHOW_TOOL, GIT_STATUS_TOOL


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root


class _Session:
    def check_capability(self, capability: str) -> str:
        return "approved"


def _assert_warning(result: ToolResult, path: Path, root: Path) -> None:
    assert result.is_error is False
    text = result.content[0].text
    assert text.startswith("WARNING: ")
    assert "outside the workspace" in text
    assert str(path.resolve()) in text
    assert str(root.resolve()) in text


def test_handler_warns_on_cwd_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    external = tmp_path / "external_repo"
    root.mkdir()
    external.mkdir()
    _assert_warning(handle_git_status(_Session(), _Workspace(root), {"cwd": str(external)}), external, root)


def test_handler_warns_on_dotdot_bypass(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _assert_warning(handle_git_status(_Session(), _Workspace(root), {"cwd": ".."}), root.parent, root)


def test_handler_rejects_non_string_cwd(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(InvalidParamsError):
        handle_git_status(_Session(), _Workspace(root), {"cwd": 42})


@pytest.mark.parametrize(
    ("handler", "params"),
    [
        (handle_git_status, {"cwd": ".."}),
        (handle_git_diff, {"cwd": ".."}),
        (handle_git_log, {"cwd": ".."}),
        (handle_git_show, {"cwd": "..", "ref": "HEAD"}),
    ],
)
def test_all_handlers_warn_on_outside_workspace_cwd(
    tmp_path: Path,
    handler: Callable[[_Session, _Workspace, dict[str, object]], ToolResult],
    params: dict[str, object],
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _assert_warning(handler(_Session(), _Workspace(root), params), root.parent, root)


def test_git_read_schemas_declare_cwd() -> None:
    specs = {spec.metadata.definition.name.value: spec for spec in git_exec_specs()}
    for name in (GIT_STATUS_TOOL, GIT_DIFF_TOOL, GIT_LOG_TOOL, GIT_SHOW_TOOL):
        properties = specs[name].metadata.definition.input_schema.get("properties", {})
        cwd = properties["cwd"]
        assert cwd["type"] == "string"
        description = cwd["description"]
        assert "WARNING:" in description
        assert "does not execute" in description
        assert "is_error=False" in description
