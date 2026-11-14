from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.mcp_models import McpConfig
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._in_memory_transport import (
    drive_request,
    parse_sse_data,
)
from ralph.mcp.server.runtime import McpServer
from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools._exec_run_deps import CommandRunner, ExecRunDeps
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.names import RalphToolName
from ralph.mcp.webvisit.extractor import ExtractedPage
from ralph.mcp.webvisit.fetcher import FetchOutcome
from ralph.workspace.fs import FsWorkspace
from tests._support.typed_accessors import must_dict_list, must_mapping, must_str_list

if TYPE_CHECKING:
    from ralph.config.web_search_config import WebSearchConfig
    from ralph.mcp.websearch.backends.base import SearchResult, WebSearchBackend


def _all_capabilities() -> set[str]:
    return {cap.value for cap in Capability}


def _build_server(
    workspace_root: Path,
    *,
    session_id: str = "sweep-session",
    run_id: str = "sweep-run",
) -> tuple[McpServer, AgentSession]:
    session = AgentSession(
        session_id=session_id,
        run_id=run_id,
        drain=SessionDrain.DEVELOPMENT.value,
        capabilities=_all_capabilities(),
    )
    workspace = FsWorkspace(workspace_root)
    registry = build_ralph_tool_registry(session, workspace, mcp_config=McpConfig())
    return McpServer(session, workspace, registry), session


def _seed_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "hello.txt").write_text("hello world\n", encoding="utf-8")
    (workspace / "subdir").mkdir(exist_ok=True)
    (workspace / "subdir" / "nested.txt").write_text("nested content\n", encoding="utf-8")
    _tiny_png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x00\x03\x00\x01"
        b"\x9d\x82\xa4\x9c"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (workspace / "screenshot.png").write_bytes(_tiny_png)


def _drive_tools_list(server: McpServer) -> list[str]:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    _status, _headers, body = drive_request(server, payload)
    data = parse_sse_data(body)
    result = must_mapping(data.get("result", {}))
    tools_block = must_dict_list(must_mapping(result, field="result")["tools"])
    return sorted(must_str_list([entry["name"] for entry in tools_block]))


def _make_in_memory_runner() -> CommandRunner:

    def runner(
        argv: list[str],
        cwd: Path,
        timeout_seconds: float | None,
    ) -> _CompletedProcessAdapter:
        del argv, cwd, timeout_seconds
        return _CompletedProcessAdapter(
            stdout=b"",
            stderr=b"",
            returncode=0,
            truncated=False,
        )

    return runner


def _patch_exec_handlers_with_in_memory_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_in_memory_runner()
    deps = ExecRunDeps(runner=runner)
    import ralph.mcp.tools.exec as _exec_mod
    import ralph.mcp.tools.unsafe_exec as _unsafe_exec_mod

    real_handle_exec_command = _exec_mod.handle_exec_command
    real_handle_unsafe_exec = _unsafe_exec_mod.handle_unsafe_exec

    def patched_exec_command(session: object, workspace: object, params: object) -> object:
        return real_handle_exec_command(session, workspace, params, deps=deps)

    def patched_unsafe_exec(session: object, workspace: object, params: object) -> object:
        return real_handle_unsafe_exec(session, workspace, params, deps=deps)

    monkeypatch.setattr(_exec_mod, "handle_exec_command", patched_exec_command)
    monkeypatch.setattr(_unsafe_exec_mod, "handle_unsafe_exec", patched_unsafe_exec)


class _FakeWebSearchBackend:
    @staticmethod
    def search(query: str, *, limit: int = 10) -> list[SearchResult]:
        del query, limit
        return []


def _fake_web_search_backend_factory(name: str, config: WebSearchConfig) -> WebSearchBackend:
    del name, config
    return _FakeWebSearchBackend()


def _fake_web_visit_fetch(url: str, **_kwargs: object) -> FetchOutcome:
    return FetchOutcome(
        status="ok",
        effective_url=url,
        http_status=200,
        content_type="text/html; charset=utf-8",
        body=b"<html><body>mocked</body></html>",
    )


def _fake_web_visit_extract(*_args: object, **_kwargs: object) -> ExtractedPage:
    return ExtractedPage(
        title="Mocked page",
        text="mocked content body",
        links=(),
    )


def _install_web_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    import ralph.mcp.tools.websearch as _websearch_handler_module
    import ralph.mcp.tools.webvisit as _webvisit_handler_module

    monkeypatch.setattr(
        _websearch_handler_module,
        "build_backend",
        _fake_web_search_backend_factory,
    )
    monkeypatch.setattr(_webvisit_handler_module, "fetch_url", _fake_web_visit_fetch)
    monkeypatch.setattr(_webvisit_handler_module, "extract_readable", _fake_web_visit_extract)


SWEEP_CALLS: dict[str, dict[str, object]] = {
    RalphToolName.READ_FILE: {"path": "hello.txt"},
    RalphToolName.READ_MULTIPLE_FILES: {"paths": ["hello.txt", "subdir/nested.txt"]},
    RalphToolName.STAT_PATH: {"path": "hello.txt"},
    RalphToolName.LIST_ALLOWED_ROOTS: {},
    RalphToolName.LIST_DIRECTORY: {"path": "."},
    RalphToolName.LIST_DIRECTORY_RECURSIVE: {"path": "."},
    RalphToolName.DIRECTORY_TREE: {"path": "."},
    RalphToolName.SEARCH_FILES: {"pattern": "hello", "path": "."},
    RalphToolName.GREP_FILES: {"pattern": "hello", "path": ".", "regex": False},
    RalphToolName.WRITE_FILE: {"path": "sweep.txt", "content": "sweep\n"},
    RalphToolName.EDIT_FILE: {
        "path": "hello.txt",
        "edits": [{"oldText": "hello world\n", "newText": "hello sweep\n"}],
    },
    RalphToolName.APPEND_FILE: {"path": "hello.txt", "content": "sweep line\n"},
    RalphToolName.CREATE_DIRECTORY: {"path": "sweep_subdir"},
    RalphToolName.MOVE_FILE: {"src": "sweep.txt", "dest": "sweep_moved.txt"},
    RalphToolName.READ_MEDIA: {"path": "screenshot.png"},
    RalphToolName.READ_IMAGE: {"path": "screenshot.png"},
    RalphToolName.MEDIA_CAPTURE: {"target": "checkout"},
    RalphToolName.COPY_FILE: {"src": "sweep_moved.txt", "dest": "sweep_copy.txt"},
    RalphToolName.DELETE_PATH: {"path": "sweep_copy.txt"},
    RalphToolName.GIT_STATUS: {},
    RalphToolName.GIT_DIFF: {},
    RalphToolName.GIT_LOG: {"count": 1},
    RalphToolName.GIT_SHOW: {"ref": "HEAD"},
    RalphToolName.EXEC: {"command": "true"},
    RalphToolName.UNSAFE_EXEC: {"command": "true", "timeout_ms": 5000},
    RalphToolName.RAW_EXEC: {"command": "true", "timeout_ms": 5000},
    RalphToolName.EXEC_WAIT: {"job_id": "sweep_job_id", "wait_ms": 1000},
    RalphToolName.EXEC_CANCEL: {"job_id": "sweep_job_id"},
    RalphToolName.RALPH_INDEX_STATUS: {},
    RalphToolName.RALPH_REINDEX: {"mode": "changed"},
    RalphToolName.RALPH_GRAPH: {"query_type": "hubs", "limit": 1},
    RalphToolName.REPORT_PROGRESS: {"status": "ok", "note": "sweep"},
    RalphToolName.READ_ENV: {"name": "PATH"},
    RalphToolName.COORDINATE: {"action": "sweep_probe"},
    RalphToolName.SUBMIT_MD_ARTIFACT: {
        "artifact_type": "development_result",
        "content": (
            "---\n"
            "type: development_result\n"
            "status: completed\n"
            "---\n\n"
            "## Summary\n- [SUM-1] sweep\n"
            "## Files Changed\n- [F-1] tmp/sweep.txt\n"
            "## Plan Items Proven\n- [S-1] sweep\n  Disposition: completed\n"
        ),
    },
    RalphToolName.VERIFY_MD_ARTIFACT: {
        "artifact_type": "development_result",
        "content": "",
    },
    RalphToolName.STAGE_MD_ARTIFACT: {
        "artifact_type": "development_result",
        "content": "draft",
        "mode": "replace_all",
    },
    RalphToolName.GET_MD_DRAFT: {"artifact_type": "development_result"},
    RalphToolName.DISCARD_MD_DRAFT: {"artifact_type": "development_result"},
    RalphToolName.FINALIZE_MD_ARTIFACT: {"artifact_type": "development_result"},
    RalphToolName.EDIT_MD_ARTIFACT: {
        "artifact_type": "development_result",
        "edits": [
            {
                "oldText": "## Summary\n- [SUM-1] sweep\n",
                "newText": "## Summary\n- [SUM-1] sweep\n- [SUM-2] edited\n",
            }
        ],
    },
    RalphToolName.WEB_SEARCH: {"query": "ralph workflow"},
    RalphToolName.VISIT_URL: {"url": "https://example.com/page"},
    RalphToolName.DOWNLOAD_URL: {
        "url": "https://example.com/data.txt",
        "output_path": "sweep_dl.txt",
    },
}


def _drive_call(server: McpServer, name: str, arguments: dict[str, object]) -> dict[str, object]:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    ).encode()
    _status, _headers, body = drive_request(server, payload)
    return parse_sse_data(body)


def _assert_call_round_trips(name: str, response: dict[str, object]) -> None:
    assert "error" not in response, (
        f"{name}: tools/call returned a JSON-RPC error envelope: {response}"
    )
    result = response.get("result")
    assert isinstance(result, dict), f"{name}: tools/call result is not a dict: {response}"
    assert result, f"{name}: tools/call result is empty: {response}"


def _alias_names_for_canonical(canonical_names: set[str], advertised: set[str]) -> list[str]:
    prefix = "mcp__ralph__"
    return sorted(
        name
        for name in advertised
        if name.startswith(prefix) and name[len(prefix) :] in canonical_names
    )


def _covered_advertised_names(advertised: set[str]) -> set[str]:
    canonical = set(SWEEP_CALLS.keys()) | {RalphToolName.DECLARE_COMPLETE}
    return canonical | set(_alias_names_for_canonical(canonical, advertised))


_TEST_TIMEOUT_SECONDS = 5


@pytest.mark.timeout_seconds(_TEST_TIMEOUT_SECONDS)
def test_every_advertised_endpoint_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_workspace(tmp_path)
    _patch_exec_handlers_with_in_memory_runner(monkeypatch)
    _install_web_backends(monkeypatch)
    server, _ = _build_server(tmp_path)
    advertised = set(_drive_tools_list(server))

    shared_targets = [
        (canonical_name, arguments)
        for canonical_name, arguments in SWEEP_CALLS.items()
        if canonical_name in advertised
    ]
    for canonical_name, arguments in shared_targets:
        response = _drive_call(server, canonical_name, arguments)
        _assert_call_round_trips(canonical_name, response)

    alias_targets = _alias_names_for_canonical(set(SWEEP_CALLS.keys()), advertised)
    for alias_name in alias_targets:
        canonical_name = alias_name[len("mcp__ralph__") :]
        arguments = SWEEP_CALLS[canonical_name]
        response = _drive_call(server, alias_name, arguments)
        _assert_call_round_trips(alias_name, response)

    fresh_server, _ = _build_server(
        tmp_path,
        session_id="sweep-terminal-session",
        run_id="sweep-terminal-run",
    )
    response = _drive_call(
        fresh_server,
        RalphToolName.DECLARE_COMPLETE,
        {"summary": "functional sweep complete"},
    )
    _assert_call_round_trips(RalphToolName.DECLARE_COMPLETE, response)


@pytest.mark.timeout_seconds(_TEST_TIMEOUT_SECONDS)
def test_every_advertised_name_has_real_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_workspace(tmp_path)
    _patch_exec_handlers_with_in_memory_runner(monkeypatch)
    _install_web_backends(monkeypatch)
    server, _ = _build_server(tmp_path)
    advertised = set(_drive_tools_list(server))

    covered = _covered_advertised_names(advertised)
    gap = advertised - covered
    assert not gap, (
        f"advertised tools with no real bridge call: {sorted(gap)}. "
        "Add a SWEEP_CALLS entry for the canonical name; the alias "
        "sweep auto-generates for ``mcp__ralph__<canonical>`` when "
        "the alias is advertised. ``declare_complete`` is covered by "
        "the terminal-call sweep on a freshly built server."
    )


def test_unknown_tool_returns_documented_error(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    server, _ = _build_server(tmp_path)
    response = _drive_call(server, "ralph_does_not_exist", {})
    assert "error" in response, f"unknown tool returned a non-error response: {response}"
    error_block = must_mapping(response["error"], field="error")
    code = error_block.get("code")
    assert code is not None, f"unknown tool: error envelope has no code: {response}"


def test_exec_handler_does_not_spawn_subprocess_under_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.process.manager import ProcessManager

    _seed_workspace(tmp_path)
    _patch_exec_handlers_with_in_memory_runner(monkeypatch)

    spawn_calls: list[list[str]] = []

    class _SpyProcessManager(ProcessManager):
        def spawn(self, argv, options):
            spawn_calls.append([str(arg) for arg in argv])
            raise AssertionError(
                "ProcessManager.spawn should not be invoked — the "
                "in-memory runner swallows exec dispatch in this sweep"
            )

    monkeypatch.setattr(
        "ralph.process.manager.get_process_manager",
        _SpyProcessManager,
        raising=True,
    )

    server, _session = _build_server(tmp_path)
    for tool_name in (
        RalphToolName.EXEC,
        RalphToolName.UNSAFE_EXEC,
        RalphToolName.RAW_EXEC,
    ):
        arguments: dict[str, object] = (
            {"command": "true"}
            if tool_name is RalphToolName.EXEC
            else {"command": "true", "timeout_ms": 5000}
        )
        response = _drive_call(server, tool_name, arguments)
        _assert_call_round_trips(tool_name, response)

    assert spawn_calls == [], (
        f"ProcessManager.spawn was reached under the sweep; "
        f"the in-memory runner must intercept all exec dispatch. "
        f"argv seen: {spawn_calls}"
    )
