from __future__ import annotations

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.names import RAW_EXEC_TOOL, UNSAFE_EXEC_TOOL
from ralph.mcp.tools.unsafe_exec import (
    PROCESS_EXEC_UNBOUNDED_CAPABILITY,
    handle_unsafe_exec,
)
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS, EXEC_MAX_TIMEOUT_MS
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot


def _raw_exec_spec():
    for spec in git_exec_specs():
        if spec.metadata.definition.name == RAW_EXEC_TOOL:
            return spec
    raise AssertionError(f"raw_exec spec is missing from {git_exec_specs!r}")


def test_raw_exec_spec_routes_to_unsafe_exec_handler() -> None:
    spec = _raw_exec_spec()
    assert spec.module_name == "ralph.mcp.tools.unsafe_exec"
    assert spec.handler_name == "handle_unsafe_exec"


def test_raw_exec_spec_requires_process_exec_unbounded() -> None:
    spec = _raw_exec_spec()
    assert spec.metadata.required_capability == McpCapability.PROCESS_EXEC_UNBOUNDED.value
    assert spec.metadata.required_capability == PROCESS_EXEC_UNBOUNDED_CAPABILITY


def test_raw_exec_and_unsafe_exec_specs_share_input_schema() -> None:
    unsafe_spec = next(
        spec for spec in git_exec_specs() if spec.metadata.definition.name == UNSAFE_EXEC_TOOL
    )
    raw_spec = _raw_exec_spec()
    assert raw_spec.metadata.definition.input_schema == unsafe_spec.metadata.definition.input_schema


def test_raw_exec_input_schema_requires_command() -> None:
    spec = _raw_exec_spec()
    schema = spec.metadata.definition.input_schema
    assert schema.get("required") == ["command"]
    properties = schema.get("properties", {})
    assert "command" in properties
    assert properties["command"].get("type") == "string"
    assert "timeout_ms" in properties


def test_raw_exec_default_timeout_matches_unsafe_exec_default() -> None:
    spec = _raw_exec_spec()
    properties = spec.metadata.definition.input_schema.get("properties", {})
    assert properties["timeout_ms"].get("default") == EXEC_DEFAULT_TIMEOUT_MS


def test_raw_exec_handler_reuses_unsafe_exec_handler() -> None:
    import ralph.mcp.tools.unsafe_exec as unsafe_exec_module

    spec = _raw_exec_spec()
    handler = getattr(unsafe_exec_module, spec.handler_name)
    assert handler is handle_unsafe_exec


def test_raw_exec_handler_rejects_empty_command(tmp_path) -> None:
    from ralph.mcp.tools.coordination import InvalidParamsError

    session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
    workspace = MockWorkspaceRoot(tmp_path)
    with __import__("pytest").raises(InvalidParamsError):
        handle_unsafe_exec(session, workspace, {"command": ""})


def test_raw_exec_handler_requires_unbounded_capability(tmp_path) -> None:
    from ralph.mcp.tools.coordination import CapabilityDeniedError

    session = MockSession(set())
    workspace = MockWorkspaceRoot(tmp_path)
    with __import__("pytest").raises(CapabilityDeniedError):
        handle_unsafe_exec(session, workspace, {"command": "echo hi"})


def test_raw_exec_handler_blocks_git_command(tmp_path) -> None:
    from ralph.mcp.tools.coordination import CapabilityDeniedError

    session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
    workspace = MockWorkspaceRoot(tmp_path)
    with __import__("pytest").raises(CapabilityDeniedError, match="git"):
        handle_unsafe_exec(session, workspace, {"command": "git push origin main"})


def test_raw_exec_handler_clamps_zero_timeout_to_default(tmp_path) -> None:
    from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
    from ralph.mcp.tools.exec import ExecRunDeps

    captured: list[object] = []
    session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
    workspace = MockWorkspaceRoot(tmp_path)

    def _run(_argv, _cwd, timeout_seconds):
        captured.append(timeout_seconds)
        return _CompletedProcessAdapter(stdout=b"ok", stderr=b"", returncode=0)

    handle_unsafe_exec(
        session,
        workspace,
        {"command": "echo hi", "timeout_ms": 0},
        ExecRunDeps(runner=_run),
    )
    assert captured == [EXEC_DEFAULT_TIMEOUT_MS / 1000]


def test_raw_exec_handler_caps_oversized_timeout(tmp_path) -> None:
    from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
    from ralph.mcp.tools.exec import ExecRunDeps

    captured: list[object] = []
    session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
    workspace = MockWorkspaceRoot(tmp_path)

    def _run(_argv, _cwd, timeout_seconds):
        captured.append(timeout_seconds)
        return _CompletedProcessAdapter(stdout=b"ok", stderr=b"", returncode=0)

    handle_unsafe_exec(
        session,
        workspace,
        {"command": "echo hi", "timeout_ms": EXEC_MAX_TIMEOUT_MS * 10},
        ExecRunDeps(runner=_run),
    )
    assert captured == [EXEC_MAX_TIMEOUT_MS / 1000]
