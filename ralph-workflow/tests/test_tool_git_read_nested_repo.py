"""Handler-level cwd boundary tests for the git read tools."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

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


def _call_with_mocked_git(
    handler: Callable[[_Session, _Workspace, dict[str, object]], ToolResult],
    workspace: _Workspace,
    params: dict[str, object],
) -> ToolResult:
    with (
        patch("ralph.mcp.tools.git_read.run_git_command", return_value="git output"),
        patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=subprocess.CompletedProcess([], 0, stdout=b"git output", stderr=b""),
        ),
    ):
        return handler(_Session(), workspace, params)


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
    _assert_warning(
        _call_with_mocked_git(handle_git_status, _Workspace(root), {"cwd": str(external)}), external, root
    )


def test_handler_warns_on_dotdot_bypass(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _assert_warning(_call_with_mocked_git(handle_git_status, _Workspace(root), {"cwd": ".."}), root.parent, root)


def test_handler_rejects_non_string_cwd(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(InvalidParamsError):
        handle_git_status(_Session(), _Workspace(root), {"cwd": 42})


def test_handler_names_an_embedded_nul_in_cwd(tmp_path: Path) -> None:
    """Left to ``Path.resolve``, this is a bare ``ValueError: lstat: embedded
    null character in path`` — an internal-looking error naming neither the
    tool nor the parameter the caller has to fix."""
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(InvalidParamsError, match="embedded NUL"):
        handle_git_status(_Session(), _Workspace(root), {"cwd": "sub/di\x00r"})


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
    _assert_warning(_call_with_mocked_git(handler, _Workspace(root), params), root.parent, root)


@pytest.mark.parametrize(
    ("handler", "params", "runner_name"),
    [
        (handle_git_status, {}, "run_git_command"),
        (handle_git_diff, {}, "run_git_command_lenient"),
        (handle_git_log, {}, "run_git_command"),
        (handle_git_show, {"ref": "HEAD"}, "run_git_command"),
    ],
)
def test_handler_warn_and_execute_when_cwd_outside_workspace(
    tmp_path: Path,
    handler: Callable[[_Session, _Workspace, dict[str, object]], ToolResult],
    params: dict[str, object],
    runner_name: str,
) -> None:
    root = tmp_path / "workspace"
    external = tmp_path / "external_repo"
    root.mkdir()
    external.mkdir()
    command_runner = Mock(return_value="git output")
    lenient_runner = Mock(
        return_value=subprocess.CompletedProcess([], 0, stdout=b"git output", stderr=b"")
    )
    with (
        patch(
            "ralph.mcp.tools.git_read._resolve_git_cwd",
            return_value=(external.resolve(), True, external.resolve()),
        ),
        patch("ralph.mcp.tools.git_read.run_git_command", command_runner),
        patch("ralph.mcp.tools.git_read.run_git_command_lenient", lenient_runner),
    ):
        result = handler(_Session(), _Workspace(root), {"cwd": str(external), **params})

    assert result.is_error is False
    assert len(result.content) == 2
    warning, output = result.content
    assert warning.text.startswith("WARNING: ")
    assert str(external.resolve()) in warning.text
    assert str(root.resolve()) in warning.text
    assert output.text == "git output"
    runner = command_runner if runner_name == "run_git_command" else lenient_runner
    runner.assert_called_once()
    assert runner.call_args.kwargs["cwd"] == external.resolve()


def test_git_read_schemas_declare_cwd() -> None:
    specs = {spec.metadata.definition.name.value: spec for spec in git_exec_specs()}
    for name in (GIT_STATUS_TOOL, GIT_DIFF_TOOL, GIT_LOG_TOOL, GIT_SHOW_TOOL):
        properties = specs[name].metadata.definition.input_schema.get("properties", {})
        cwd = properties["cwd"]
        assert cwd["type"] == "string"
        description = cwd["description"]
        assert "WARNING:" in description
        assert "still runs" in description
        assert "is_error=False" in description
