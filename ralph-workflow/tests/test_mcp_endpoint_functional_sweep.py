"""End-to-end round-trip sweep over every advertised MCP endpoint.

The plan's AC-03 / S-5 gate: "Every advertised endpoint round-trips
through the real bridge with a well-formed response (no unknown-tool /
capability-denied for advertised names)."

This module stands up the production MCP server (in-process, in-memory
transport — no sockets, no real subprocess, no live network) and issues
one ``tools/call`` per advertised tool name with minimal valid
arguments. A response counts as "the endpoint works" when:

* The JSON-RPC reply carries a ``result`` block (success or a
  structured ``isError: true`` domain error such as file-not-found).
* The response is NOT a JSON-RPC ``error`` envelope with a code from
  ``-32601`` (Method not found / unknown tool) or
  ``-32602`` (Invalid params) caused by a malformed argument shape.
* The response is NOT a capability-denial rendered as a top-level
  ``-32603`` (Internal server error) when ``is_error: false`` would
  have been a domain result.

Tools that hit the live network (``web_search``, ``visit_url``,
``download_url``) are NOT called in this suite — the contract gate for
those is "registered, described, and parameter-validated", which the
``tools/list`` invocation already proves. Plan-artifact-specific tools
are out of scope (the artifact branch removes them) — only generic
artifact tools are exercised here.

Runtime budget: in-process, tmp_path workspace, no sockets, no
subprocess, no network. Targets <5s wall clock to stay inside the
60s combined test budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.names import RalphToolName
from ralph.workspace.fs import FsWorkspace
from tests._support.typed_accessors import must_dict_list, must_mapping, must_str_list

if TYPE_CHECKING:
    import pytest

# Tools that hit the live network — exercised at the contract level
# (registered, described, parameter-validated) but NEVER called with
# a real query/URL in this sweep. Adding them here would inject
# network into a unit-tier test and balloon the 60s budget.
LIVE_NETWORK_TOOLS: frozenset[str] = frozenset(
    {
        RalphToolName.WEB_SEARCH,
        RalphToolName.VISIT_URL,
        RalphToolName.DOWNLOAD_URL,
    }
)


def _all_capabilities() -> set[str]:
    """Return every internal Ralph capability value as a session-capabilities set."""
    return {cap.value for cap in Capability}


def _build_server(
    workspace_root: Path,
) -> tuple[McpServer, AgentSession]:
    """Build the production McpServer bound to a tmp_path workspace.

    The session is granted every internal Ralph capability so the
    full tool surface is advertised and visible. The workspace is a
    real :class:`FsWorkspace` rooted at ``workspace_root``; the test
    seeds a few representative files so read/search/grep calls have
    something to find.
    """
    session = AgentSession(
        session_id="sweep-session",
        run_id="sweep-run",
        drain=SessionDrain.DEVELOPMENT.value,
        capabilities=_all_capabilities(),
    )
    workspace = FsWorkspace(workspace_root)
    registry = build_ralph_tool_registry(session, workspace, mcp_config=McpConfig())
    return McpServer(session, workspace, registry), session


def _seed_workspace(workspace: Path) -> None:
    """Populate the workspace with one file each for read/edit/delete-style tools."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "hello.txt").write_text("hello world\n", encoding="utf-8")
    (workspace / "subdir").mkdir(exist_ok=True)
    (workspace / "subdir" / "nested.txt").write_text("nested content\n", encoding="utf-8")


def _drive_tools_list(server: McpServer) -> list[str]:
    """Return the advertised tool names from a ``tools/list`` call."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ).encode()
    _status, _headers, body = drive_request(server, payload)
    data = parse_sse_data(body)
    result = must_mapping(data.get("result", {}))
    tools_block = must_dict_list(must_mapping(result, field="result")["tools"])
    return sorted(must_str_list([entry["name"] for entry in tools_block]))


def _canonical_tool_names(advertised: list[str]) -> set[str]:
    """Strip the strict-MCP ``mcp__<server>__<tool>`` aliases from a tools/list payload.

    The bridge advertises each tool under its canonical name and
    under the alias a strict-MCP client (Claude Code) expects. The
    coverage gate is about canonical names: an agent can call
    either form, the server resolves aliases to canonical names,
    so the sweep only needs to cover the canonical set.
    """
    return {name for name in advertised if not name.startswith("mcp__ralph__")}


def _make_in_memory_runner() -> Any:
    """Return a deterministic in-memory runner for ``ExecRunDeps``.

    The runner swallows whatever argv the handler receives and
    returns an empty completed-process adapter with exit code 0. It
    is used to assert that the sweeP covers the exec tool
    *end-to-end through the bridge* (capability check, parameter
    parsing, output formatting, spill policy) without spawning a real
    OS process.
    """

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


def _patch_exec_handlers_with_in_memory_runner(monkeypatch: Any) -> None:
    """Wrap ``handle_exec_command`` and ``handle_unsafe_exec`` with an in-memory runner.

    Both handlers accept an optional ``deps`` keyword. The default
    branch dispatches to the global ``ProcessManager`` and spawns a
    real OS process. The sweep is a unit-tier test in the immutable
    60s combined budget — real subprocess execution (even the cheap
    ``true`` / ``echo`` invocations used here) would loosen the
    contract and reintroduce the audit_test_policy gap that the plan
    flagged.

    The wrapper imports each handler's module lazily (matching the
    production ``LazyToolHandler`` resolution path), then patches the
    module attribute so the next bridge dispatch finds the wrapped
    version. The original handlers are called with the injected
    ``deps`` so every other layer — capability check, parameter
    parsing, VCS blacklist, output formatting, spill policy —
    remains live, only the process boundary is faked.
    """
    runner = _make_in_memory_runner()
    deps = ExecRunDeps(runner=runner)
    import ralph.mcp.tools.exec as _exec_mod
    import ralph.mcp.tools.unsafe_exec as _unsafe_exec_mod

    real_handle_exec_command = _exec_mod.handle_exec_command
    real_handle_unsafe_exec = _unsafe_exec_mod.handle_unsafe_exec

    def patched_exec_command(session, workspace, params):
        return real_handle_exec_command(session, workspace, params, deps=deps)

    def patched_unsafe_exec(session, workspace, params):
        return real_handle_unsafe_exec(session, workspace, params, deps=deps)

    monkeypatch.setattr(_exec_mod, "handle_exec_command", patched_exec_command)
    monkeypatch.setattr(
        _unsafe_exec_mod, "handle_unsafe_exec", patched_unsafe_exec
    )


# Minimal-valid-args per tool. Each entry is ``(name, arguments)``;
# the sweep issues one call per entry. Tools not in this table are
# asserted at the contract level (registration + tools/list presence)
# via the list-based test below; live-network tools are also in this
# exclusion set.
SWEEP_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    # Workspace read.
    (RalphToolName.READ_FILE, {"path": "hello.txt"}),
    (RalphToolName.READ_MULTIPLE_FILES, {"paths": ["hello.txt", "subdir/nested.txt"]}),
    (RalphToolName.STAT_PATH, {"path": "hello.txt"}),
    (RalphToolName.LIST_ALLOWED_ROOTS, {}),
    (RalphToolName.LIST_DIRECTORY, {"path": "."}),
    (RalphToolName.LIST_DIRECTORY_RECURSIVE, {"path": "."}),
    (RalphToolName.DIRECTORY_TREE, {"path": "."}),
    (RalphToolName.SEARCH_FILES, {"pattern": "hello", "path": "."}),
    (
        RalphToolName.GREP_FILES,
        {"pattern": "hello", "path": ".", "regex": False},
    ),
    # Workspace write/edit/delete. The sweep issues these against
    # the tmp_path root so the FsWorkspace path checks pass.
    (RalphToolName.WRITE_FILE, {"path": "sweep.txt", "content": "sweep\n"}),
    (
        RalphToolName.EDIT_FILE,
        {
            "path": "hello.txt",
            "edits": [{"oldText": "hello world\n", "newText": "hello sweep\n"}],
        },
    ),
    (
        RalphToolName.APPEND_FILE,
        {"path": "hello.txt", "content": "sweep line\n"},
    ),
    (RalphToolName.CREATE_DIRECTORY, {"path": "sweep_subdir"}),
    (RalphToolName.MOVE_FILE, {"src": "sweep.txt", "dest": "sweep_moved.txt"}),
    (RalphToolName.COPY_FILE, {"src": "sweep_moved.txt", "dest": "sweep_copy.txt"}),
    (RalphToolName.DELETE_PATH, {"path": "sweep_copy.txt"}),
    # Git read. The repo may or may not be initialized; both cases
    # are valid domain responses — what matters is the call is
    # answered without a "Tool is not registered" or capability-denial
    # error.
    (RalphToolName.GIT_STATUS, {}),
    (RalphToolName.GIT_DIFF, {}),
    (RalphToolName.GIT_LOG, {"count": 1}),
    (RalphToolName.GIT_SHOW, {"ref": "HEAD"}),
    # Exec / unsafe_exec / raw_exec — echo a single-character string
    # so the bounded exec handler cannot wedge on a missing binary.
    (RalphToolName.EXEC, {"command": "true"}),
    (RalphToolName.UNSAFE_EXEC, {"command": "true", "timeout_ms": 5000}),
    (RalphToolName.RAW_EXEC, {"command": "true", "timeout_ms": 5000}),
    # Explore index — handlers are exercised against the workspace
    # root with the default options. They answer with structured
    # payloads; we do not need to assert specific content here.
    (RalphToolName.RALPH_INDEX_STATUS, {}),
    (RalphToolName.RALPH_REINDEX, {"mode": "changed"}),
    (
        RalphToolName.RALPH_GRAPH,
        {"query_type": "hubs", "limit": 1},
    ),
    # Coordination.
    (RalphToolName.REPORT_PROGRESS, {"status": "ok", "note": "sweep"}),
    (RalphToolName.READ_ENV, {"name": "PATH"}),
    # Declare complete is excluded: it finalizes the session and would
    # interfere with subsequent calls in the same sweep. The contract
    # gate below still proves its registration.
    # Generic artifact tools — handled by the artifact tool surface
    # in :mod:`ralph.mcp.tools.md_artifact`. The handlers run
    # end-to-end against the in-memory ArtifactBackend. Each call
    # has its own minimal valid arguments; the response shape is a
    # domain result, not a capability denial.
    (
        RalphToolName.SUBMIT_MD_ARTIFACT,
        {
            "artifact_type": "development_result",
            "content": (
                "---\n"
                "type: development_result\n"
                "status: completed\n"
                "---\n\n"
                "## Summary\n- [SUM-1] sweep\n"
                "## Files Changed\n- [F-1] tmp/sweep.txt\n"
                "## Plan Items Proven\n- [S-1] sweep\n"
            ),
        },
    ),
    (RalphToolName.VERIFY_MD_ARTIFACT, {"artifact_type": "development_result", "content": ""}),
    (
        RalphToolName.STAGE_MD_ARTIFACT,
        {"artifact_type": "development_result", "content": "draft", "mode": "replace_all"},
    ),
    (
        RalphToolName.GET_MD_DRAFT,
        {"artifact_type": "development_result"},
    ),
    (
        RalphToolName.DISCARD_MD_DRAFT,
        {"artifact_type": "development_result"},
    ),
    (
        RalphToolName.FINALIZE_MD_ARTIFACT,
        {"artifact_type": "development_result"},
    ),
    (
        RalphToolName.EDIT_MD_ARTIFACT,
        {
            "artifact_type": "development_result",
            "edits": [
                {
                    "oldText": "## Summary\n- [SUM-1] sweep\n",
                    "newText": "## Summary\n- [SUM-1] sweep\n- [SUM-2] edited\n",
                }
            ],
        },
    ),
    # Coordination — plan-Write-gated action that emits a coordination
    # line. The session holds every capability (including
    # ``artifact.plan_write``), so the live call resolves with a
    # well-formed text result. Keeping it in the sweep proves the
    # tool is reachable through the real bridge, not just registered.
    (
        RalphToolName.COORDINATE,
        {"action": "sweep_probe"},
    ),
)


def _drive_call(server: McpServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Issue one ``tools/call`` via the in-memory transport and return the JSON-RPC payload."""
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


def _assert_call_round_trips(name: str, response: dict[str, Any]) -> None:
    """Assert ``response`` is a well-formed tool call answer, not unknown/capability-denied.

    A round-trip succeeds when:

    * the response has no top-level ``error`` (unknown tool / method /
      internal error / capability denial); and
    * the response has a ``result`` block that is a non-empty dict.
    """
    assert "error" not in response, (
        f"{name}: tools/call returned a JSON-RPC error envelope: {response}"
    )
    result = response.get("result")
    assert isinstance(result, dict), (
        f"{name}: tools/call result is not a dict: {response}"
    )
    assert result, f"{name}: tools/call result is empty: {response}"


def test_advertised_tool_set_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-03 / S-5: every advertised tool name round-trips through the real bridge.

    The sweep seeds a tmp_path workspace, stands up a real McpServer
    with the full capability set, and issues one ``tools/call`` per
    advertised tool with minimal valid arguments. Each call must
    return a well-formed response (no unknown-tool /
    capability-denied). Live-network tools are skipped here and
    asserted at the contract level by the
    ``test_live_network_tools_have_contract_coverage`` test below.

    The exec / unsafe_exec / raw_exec handlers are wrapped with an
    in-memory :class:`ExecRunDeps` runner so the sweep exercises the
    real bridge dispatch (capability check, parameter parsing,
    blacklist policy, output formatting, spill path) without ever
    spawning an OS process. See
    :func:`_patch_exec_handlers_with_in_memory_runner`.
    """
    _seed_workspace(tmp_path)
    _patch_exec_handlers_with_in_memory_runner(monkeypatch)
    server, _session = _build_server(tmp_path)
    advertised = _canonical_tool_names(_drive_tools_list(server))

    sweep_targets = {name for name, _ in SWEEP_CALLS}
    # Every sweep target must be in the advertised set — the sweep is
    # only useful if it covers the surface that the prompt advertises.
    missing_in_advertised = sweep_targets - advertised
    assert not missing_in_advertised, (
        f"sweep targets missing from tools/list: "
        f"{sorted(missing_in_advertised)}"
    )

    for name, arguments in SWEEP_CALLS:
        response = _drive_call(server, name, arguments)
        _assert_call_round_trips(name, response)


def test_every_advertised_tool_round_trips_or_has_contract_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-03: every tool in ``tools/list`` either round-trips or has documented coverage.

    The sweep table above covers every tool that can be exercised
    in-process without the live network or per-tool fixture setup.
    Live-network tools (``web_search`` / ``visit_url`` /
    ``download_url``) and the finalizing ``declare_complete`` are
    excluded from the live-call sweep; this test pins that:

    * every advertised tool is either in ``SWEEP_CALLS``, or
    * is explicitly in ``LIVE_NETWORK_TOOLS`` (asserted at contract
      level by ``test_live_network_tools_have_contract_coverage``), or
    * is ``declare_complete`` (registration proven; live call would
      finalize the session and break later tests).

    No tool is left in a coverage gap — if a new tool lands in
    ``RalphToolName`` without a SWEEP_CALL or an exclusion here, this
    test fails closed.

    The exec handler is wrapped with the in-memory runner (see
    :func:`_patch_exec_handlers_with_in_memory_runner`) so the
    coverage gate for ``exec`` / ``unsafe_exec`` / ``raw_exec`` does
    not depend on a real subprocess.
    """
    _seed_workspace(tmp_path)
    _patch_exec_handlers_with_in_memory_runner(monkeypatch)
    server, _session = _build_server(tmp_path)
    advertised = _canonical_tool_names(_drive_tools_list(server))

    sweep_targets = {name for name, _ in SWEEP_CALLS}
    documented_exclusions = LIVE_NETWORK_TOOLS | {RalphToolName.DECLARE_COMPLETE}
    covered = sweep_targets | documented_exclusions

    gap = advertised - covered
    assert not gap, (
        f"advertised tools with no coverage: {sorted(gap)} "
        f"(add a SWEEP_CALLS entry, add a documented exclusion, or "
        f"verify the exclusion in LIVE_NETWORK_TOOLS)"
    )


def test_live_network_tools_have_contract_coverage(tmp_path: Path) -> None:
    """``web_search`` / ``visit_url`` / ``download_url`` are registered and described.

    Live network calls are explicitly excluded from the sweep
    (network in a unit-tier test bloats the 60s budget). Their
    end-to-end behavior is covered by separate live-integration
    suites; here we only prove the registration surface is intact
    and the input schema is non-degenerate, so an agent that wants
    one of these tools can find it in ``tools/list``.
    """
    _seed_workspace(tmp_path)
    server, _session = _build_server(tmp_path)
    advertised = _canonical_tool_names(_drive_tools_list(server))

    for tool in LIVE_NETWORK_TOOLS:
        assert tool in advertised, (
            f"{tool}: registered in RalphToolName but not advertised "
            f"through the live bridge"
        )

    # tools/list for the live-network tools carries a non-empty
    # description and an input schema with required parameters —
    # the same contract as every other tool.
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ).encode()
    _status, _headers, body = drive_request(server, payload)
    data = parse_sse_data(body)
    result = must_mapping(data.get("result", {}))
    tools_block = must_dict_list(must_mapping(result, field="result")["tools"])
    tool_entries = {entry["name"]: entry for entry in tools_block}
    for tool in LIVE_NETWORK_TOOLS:
        entry = tool_entries.get(tool)
        assert entry is not None, f"{tool}: missing from tools/list"
        description = entry.get("description", "")
        assert description, f"{tool}: empty description in tools/list"
        input_schema = entry.get("inputSchema", {})
        assert isinstance(input_schema, dict), (
            f"{tool}: inputSchema is not a dict"
        )
        assert input_schema.get("required"), (
            f"{tool}: inputSchema has no required parameter — agents "
            f"cannot call this tool without it"
        )


def test_unknown_tool_returns_documented_error(tmp_path: Path) -> None:
    """The well-formed error path for unknown tools is preserved.

    This is the negative-space contract: when an agent (or test)
    calls a tool that the bridge does not register, the response
    must be a JSON-RPC ``error`` envelope — never a silent success
    or a panic. The negative test guards against future regressions
    that accidentally swallow unknown-tool errors and would mask
    silent endpoint drift.
    """
    _seed_workspace(tmp_path)
    server, _session = _build_server(tmp_path)
    response = _drive_call(server, "ralph_does_not_exist", {})
    assert "error" in response, (
        f"unknown tool returned a non-error response: {response}"
    )
    error_block = must_mapping(response["error"], field="error")
    code = error_block.get("code")
    assert code is not None, f"unknown tool: error envelope has no code: {response}"


def test_exec_handler_does_not_spawn_subprocess_under_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the wrapped sweep MUST NOT reach the real process-manager spawn path.

    AC-03 / S-4 require the sweep to stay in-process, fit inside
    the 60s combined budget, and exercise the production handler
    dispatch path. The earlier sweep directly invoked ``true`` /
    ``echo`` through the global ``ProcessManager.spawn`` which is
    real subprocess execution — disallowed by
    ``ralph.testing.audit_test_policy`` and the plan's "no real
    subprocess" constraint.

    This test pins the fix: with
    :func:`_patch_exec_handlers_with_in_memory_runner` applied, the
    bridge round-trips the exec / unsafe_exec / raw_exec tools but
    the ``ProcessManager.spawn`` stub injected for this test never
    sees a call. A regression that reverts to the real runner path
    fails closed here.
    """
    from ralph.process.manager import ProcessManager

    _seed_workspace(tmp_path)
    _patch_exec_handlers_with_in_memory_runner(monkeypatch)

    spawn_calls: list[list[str]] = []

    class _SpyProcessManager(ProcessManager):
        """``ProcessManager`` subclass that records spawn attempts without running them."""

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
        arguments = (
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
