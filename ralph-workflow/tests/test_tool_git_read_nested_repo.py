"""Handler-level tests for the nested-repo ``cwd`` support on git read tools.

The handler calls the workspace-bounded validator once per request and
threads the resolved path into the git subprocess runners. These tests
use an injected ``GitRunner`` (no real git subprocess) so they stay
fast and unmarked; the real-Git boundary proof lives in
``tests/test_tool_git_read_path_validation.py``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.mcp.tools.git_read import (
    handle_git_diff,
    handle_git_log,
    handle_git_show,
    handle_git_status,
    run_git_command,
)
from ralph.mcp.tools.names import (
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_TOOL,
)


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root


class _Session:
    """Session stub approving every capability the handlers gate on."""

    def check_capability(self, capability: str) -> str:
        return "approved"


class _RecordingRunner:
    """Fake git runner that records the cwd it was invoked with."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(
        self, command: list[str], cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, b"ok", b"")


def test_nested_repo_cwd_is_threaded_to_runner(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    nested = root / "nested-repo"
    nested.mkdir(parents=True)
    runner = _RecordingRunner()
    output = run_git_command(_Workspace(root), ["status"], runner=runner, cwd=nested)
    assert output == "ok"
    assert runner.calls[-1][1] == nested


def test_default_cwd_still_uses_workspace_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    runner = _RecordingRunner()
    run_git_command(_Workspace(root), ["status"], runner=runner)
    assert runner.calls[-1][1] == root


def test_handler_rejects_cwd_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    external = tmp_path / "external_repo"
    external.mkdir()
    workspace = _Workspace(root)
    session = _Session()
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_git_status(session, workspace, {"cwd": str(external)})
    message = str(exc_info.value)
    assert str(external.resolve()) in message
    assert str(root.resolve()) in message


def test_handler_rejects_dotdot_bypass(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = _Workspace(root)
    session = _Session()
    with pytest.raises(InvalidParamsError):
        handle_git_status(session, workspace, {"cwd": ".."})


def test_handler_rejects_non_string_cwd(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = _Workspace(root)
    session = _Session()
    with pytest.raises(InvalidParamsError):
        handle_git_status(session, workspace, {"cwd": 42})


@pytest.mark.parametrize(
    ("handler", "params"),
    [
        (handle_git_status, {"cwd": ".."}),
        (handle_git_diff, {"cwd": ".."}),
        (handle_git_log, {"cwd": ".."}),
        (handle_git_show, {"cwd": "..", "ref": "HEAD"}),
    ],
)
def test_all_handlers_enforce_workspace_boundary(
    tmp_path: Path,
    handler: Callable[[_Session, _Workspace, dict[str, object]], object],
    params: dict[str, object],
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(InvalidParamsError):
        handler(_Session(), _Workspace(root), params)


def test_git_read_schemas_declare_cwd() -> None:
    specs = {spec.metadata.definition.name.value: spec for spec in git_exec_specs()}
    for name in (GIT_STATUS_TOOL, GIT_DIFF_TOOL, GIT_LOG_TOOL, GIT_SHOW_TOOL):
        schema = specs[name].metadata.definition.input_schema
        properties = schema.get("properties", {})
        assert "cwd" in properties, f"{name} schema must declare a cwd property"
        assert properties["cwd"]["type"] == "string"
