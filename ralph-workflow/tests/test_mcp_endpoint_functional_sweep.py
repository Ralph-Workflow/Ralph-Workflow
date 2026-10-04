"""End-to-end round-trip sweep over every advertised MCP endpoint.

The plan's AC-03 / S-5 gate: "Every advertised endpoint round-trips
through the real bridge with a well-formed response (no unknown-tool /
capability-denied for advertised names)."

This module stands up the production MCP server (in-process, in-memory
transport — no sockets, no real subprocess, no live network) and
issues one ``tools/call`` per advertised tool name with minimal
valid arguments. The advertised set is ``tools/list`` itself, so the
sweep naturally widens every time a new tool is added.

Coverage shape:

* For every advertised *canonical* name we have a SWEEP_CALLS entry
  with minimal valid arguments.
* For every advertised *alias* (the strict-MCP ``mcp__ralph__<tool>``
  form) we generate a sweep entry from the canonical entry — same
  arguments, different name going through ``_resolve_alias_to_canonical``.
* The ``declare_complete`` tool finalizes its own session, so the
  test runs it as the terminal call against a freshly built
  server/session. This pins its real-bridge behavior without
  poisoning the rest of the sweep with a completion sentinel.

A response counts as "the endpoint works" when:

* The JSON-RPC reply carries a ``result`` block (success or a
  structured ``is_error: true`` domain error such as file-not-found).
* The response is NOT a JSON-RPC ``error`` envelope with a code from
  ``-32601`` (Method not found / unknown tool) or
  ``-32602`` (Invalid params) caused by a malformed argument shape.

Web tools (``web_search`` / ``visit_url`` / ``download_url``) are
exercised against mocked backends so the sweep never opens a
real network socket — the dispatcher is the production handler,
only the upstream boundary is faked.

Runtime budget: in-process, tmp_path workspace, no sockets, no
subprocess, no network. Targets <5s wall clock to stay inside the
60s combined test budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.names import RalphToolName
from ralph.mcp.webvisit.extractor import ExtractedPage
from ralph.mcp.webvisit.fetcher import FetchOutcome
from ralph.workspace.fs import FsWorkspace
from tests._support.typed_accessors import must_dict_list, must_mapping, must_str_list


def _all_capabilities() -> set[str]:
    """Return every internal Ralph capability value as a session-capabilities set."""
    return {cap.value for cap in Capability}


def _build_server(
    workspace_root: Path,
    *,
    session_id: str = "sweep-session",
    run_id: str = "sweep-run",
) -> tuple[McpServer, AgentSession]:
    """Build the production McpServer bound to ``workspace_root``.

    The session is granted every internal Ralph capability so the
    full tool surface is advertised and visible. The session /
    run id pair is parameterised so the terminal ``declare_complete``
    call can be served by a freshly built server without colliding
    with another server's identifier.
    """
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
    """Populate the workspace with files for read/edit/delete-style tools."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "hello.txt").write_text("hello world\n", encoding="utf-8")
    (workspace / "subdir").mkdir(exist_ok=True)
    (workspace / "subdir" / "nested.txt").write_text("nested content\n", encoding="utf-8")
    # Minimal valid 1x1 PNG so read_media/read_image have a workspace file
    # the unknown-provider fallback can produce a resource_reference for.
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
    """Return the advertised tool names from a ``tools/list`` call."""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    _status, _headers, body = drive_request(server, payload)
    data = parse_sse_data(body)
    result = must_mapping(data.get("result", {}))
    tools_block = must_dict_list(must_mapping(result, field="result")["tools"])
    return sorted(must_str_list([entry["name"] for entry in tools_block]))


def _make_in_memory_runner() -> Any:
    """Return a deterministic in-memory runner for ``ExecRunDeps``.

    The runner swallows whatever argv the handler receives and
    returns an empty completed-process adapter with exit code 0.
    It is used to assert that the sweep covers the exec tool
    *end-to-end through the bridge* (capability check, parameter
    parsing, output formatting, spill policy) without spawning a
    real OS process.
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
    monkeypatch.setattr(_unsafe_exec_mod, "handle_unsafe_exec", patched_unsafe_exec)


# Mocked web backend fixtures — provided to the production handlers
# via the same module-attribute seam the existing web-access tests
# use (``monkeypatch.setattr(<handler_module>, "fetch_url", ...``).
# These keep the sweep in-process; the production handler dispatch
# chain stays live, only the upstream boundary is faked.


class _FakeWebSearchBackend:
    """Stand-in for a real ``WebSearchBackend`` (no network)."""

    @staticmethod
    def search(query: str, *, limit: int = 10) -> list[Any]:
        del query, limit
        return []


def _fake_web_search_backend_factory(name: str, config: Any) -> Any:
    """Replacement factory for ``ralph.mcp.tools.websearch.build_backend``."""
    del name, config
    return _FakeWebSearchBackend()


def _fake_web_visit_fetch(url: str, **_kwargs: Any) -> FetchOutcome:
    """Replacement for ``ralph.mcp.tools.webvisit.fetch_url``."""
    return FetchOutcome(
        status="ok",
        effective_url=url,
        http_status=200,
        content_type="text/html; charset=utf-8",
        body=b"<html><body>mocked</body></html>",
    )


def _fake_web_visit_extract(*_args: Any, **_kwargs: Any) -> Any:
    """Replacement for ``ralph.mcp.tools.webvisit.extract_readable``."""
    return ExtractedPage(
        title="Mocked page",
        text="mocked content body",
        links=(),
    )


def _install_web_backends(monkeypatch: Any) -> None:
    """Install in-process replacements for the web tool backends.

    The web search backend factory, the URL fetcher, and the
    readability extractor are all swapped at the public handler
    module attribute (``ralph.mcp.tools.websearch`` /
    ``ralph.mcp.tools.webvisit``). The handler bodies still run on
    the real production code path — capability check, parameter
    parsing, JSON envelope construction — only the upstream
    network/extractor boundary is faked.
    """
    import ralph.mcp.tools.websearch as _websearch_handler_module
    import ralph.mcp.tools.webvisit as _webvisit_handler_module

    monkeypatch.setattr(
        _websearch_handler_module,
        "build_backend",
        _fake_web_search_backend_factory,
    )
    monkeypatch.setattr(_webvisit_handler_module, "fetch_url", _fake_web_visit_fetch)
    monkeypatch.setattr(_webvisit_handler_module, "extract_readable", _fake_web_visit_extract)


# Minimal valid arguments for every advertised canonical tool. Each
# entry pairs with its ``mcp__ralph__<name>`` alias when the alias
# is advertised — the alias sweep reuses these arguments verbatim,
# driving a real ``tools/call`` through the alias dispatch resolver
# (``McpServer._resolve_alias_to_canonical``) so a regression that
# drops alias emission or alias resolution fails closed.
#
# ``declare_complete`` is intentionally excluded from this table; the
# terminal call sweep covers it on a freshly built server so a
# prior completion sentinel cannot poison the call.
SWEEP_CALLS: dict[str, dict[str, Any]] = {
    # Workspace read.
    RalphToolName.READ_FILE: {"path": "hello.txt"},
    RalphToolName.READ_MULTIPLE_FILES: {"paths": ["hello.txt", "subdir/nested.txt"]},
    RalphToolName.STAT_PATH: {"path": "hello.txt"},
    RalphToolName.LIST_ALLOWED_ROOTS: {},
    RalphToolName.LIST_DIRECTORY: {"path": "."},
    RalphToolName.LIST_DIRECTORY_RECURSIVE: {"path": "."},
    RalphToolName.DIRECTORY_TREE: {"path": "."},
    RalphToolName.SEARCH_FILES: {"pattern": "hello", "path": "."},
    RalphToolName.GREP_FILES: {"pattern": "hello", "path": ".", "regex": False},
    # Workspace write/edit/delete. The sweep issues these against
    # the tmp_path root so the FsWorkspace path checks pass.
    RalphToolName.WRITE_FILE: {"path": "sweep.txt", "content": "sweep\n"},
    RalphToolName.EDIT_FILE: {
        "path": "hello.txt",
        "edits": [{"oldText": "hello world\n", "newText": "hello sweep\n"}],
    },
    RalphToolName.APPEND_FILE: {"path": "hello.txt", "content": "sweep line\n"},
    RalphToolName.CREATE_DIRECTORY: {"path": "sweep_subdir"},
    RalphToolName.MOVE_FILE: {"src": "sweep.txt", "dest": "sweep_moved.txt"},
    # Multimodal tools. The handler returns a structured resource_reference
    # for the unknown-provider PDF/PNG payload it sees at runtime, so the
    # sweep only needs to confirm the bridge accepts the call and returns
    # a non-error result. The full end-to-end media linkage is covered by
    # `tests/integration/test_multimodal_end_to_end_linkage.py`.
    RalphToolName.READ_MEDIA: {"path": "screenshot.png"},
    RalphToolName.READ_IMAGE: {"path": "screenshot.png"},
    RalphToolName.MEDIA_CAPTURE: {"target": "checkout"},
    RalphToolName.COPY_FILE: {"src": "sweep_moved.txt", "dest": "sweep_copy.txt"},
    RalphToolName.DELETE_PATH: {"path": "sweep_copy.txt"},
    # Git read. The repo may or may not be initialized; both cases
    # are valid domain responses — what matters is the call is
    # answered without a "Tool is not registered" or capability-denial
    # error.
    RalphToolName.GIT_STATUS: {},
    RalphToolName.GIT_DIFF: {},
    RalphToolName.GIT_LOG: {"count": 1},
    RalphToolName.GIT_SHOW: {"ref": "HEAD"},
    # Exec / unsafe_exec / raw_exec — `true` is a single-character
    # builtin so the bounded exec handler cannot wedge on a missing
    # binary. The handler is wrapped with an in-memory runner so the
    # sweep never reaches the real ``ProcessManager.spawn`` path; the
    # runner returns an empty completed-process with exit code 0.
    RalphToolName.EXEC: {"command": "true"},
    RalphToolName.UNSAFE_EXEC: {"command": "true", "timeout_ms": 5000},
    RalphToolName.RAW_EXEC: {"command": "true", "timeout_ms": 5000},
    # Explore index — handlers are exercised against the workspace
    # root with the default options. They answer with structured
    # payloads; we do not need to assert specific content here.
    RalphToolName.RALPH_INDEX_STATUS: {},
    RalphToolName.RALPH_REINDEX: {"mode": "changed"},
    RalphToolName.RALPH_GRAPH: {"query_type": "hubs", "limit": 1},
    # Coordination.
    RalphToolName.REPORT_PROGRESS: {"status": "ok", "note": "sweep"},
    RalphToolName.READ_ENV: {"name": "PATH"},
    # Workspace coordination — gated on ``artifact.plan_write``,
    # which the all-capability session declares.
    RalphToolName.COORDINATE: {"action": "sweep_probe"},
    # Generic artifact tools — handled by the artifact tool surface
    # in :mod:`ralph.mcp.tools.md_artifact`. Each call has its own
    # minimal valid arguments; the response shape is a domain
    # result (success or ``is_error`` rejection), not a capability
    # denial.
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
    # Network tools — handlers run on the real production code path
    # against mocked backends installed by ``_install_web_backends``.
    # The dispatch chain is live; only the upstream boundary (HTTP
    # fetch, search backend, readability extraction) is faked.
    RalphToolName.WEB_SEARCH: {"query": "ralph workflow"},
    RalphToolName.VISIT_URL: {"url": "https://example.com/page"},
    RalphToolName.DOWNLOAD_URL: {
        "url": "https://example.com/data.txt",
        "output_path": "sweep_dl.txt",
    },
}


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
    assert isinstance(result, dict), f"{name}: tools/call result is not a dict: {response}"
    assert result, f"{name}: tools/call result is empty: {response}"


def _alias_names_for_canonical(canonical_names: set[str], advertised: set[str]) -> list[str]:
    """Return the advertised alias names whose canonical counterpart is in ``canonical_names``.

    Aliases are emitted by ``McpServer._alias_for_tool_name`` for every
    known ``RalphToolName``. The strict-MCP alias form is
    ``mcp__<server>__<tool>``; this helper only keeps aliases whose
    canonical ``<tool>`` part is in the requested set AND whose full
    advertised name is present (so a brand-new tool whose alias emission
    silently regresses still fails the coverage gap assertion, not the
    round-trip assertion).
    """
    prefix = "mcp__ralph__"
    return sorted(
        name
        for name in advertised
        if name.startswith(prefix) and name[len(prefix) :] in canonical_names
    )


def _covered_advertised_names(advertised: set[str]) -> set[str]:
    """Compute the canonical+alias names the sweep will actually call.

    ``SWEEP_CALLS`` provides canonical coverage; the alias sweep
    auto-generates ``mcp__ralph__<tool>`` entries for every alias
    whose canonical form has a SWEEP_CALLS entry; ``declare_complete``
    is covered by the terminal call on a freshly built server.

    Together these must equal the advertised set. The equality check
    lives in ``test_every_advertised_name_has_real_call``.
    """
    canonical = set(SWEEP_CALLS.keys()) | {RalphToolName.DECLARE_COMPLETE}
    return canonical | set(_alias_names_for_canonical(canonical, advertised))


#: Per-test budget for the full functional sweep. With ~80 advertised
#: names (canonical + aliases) plus the network-aliased sweep, the
#: in-process round-trips sit at ~1.5-2.0s of wall-clock in isolation.
#: Under xdist contention the test can spike past the 1.0s default
#: per-test cap. The marker opts into a 5s budget — well inside the
#: immutable 60s combined test budget and far below any per-test
#: ceiling on the audit_test_policy side (no real subprocess, no
#: real network). Without the marker the suite fails intermittently
#: with ``TestExecutionTimeoutError`` on a busy host.
_TEST_TIMEOUT_SECONDS = 5


@pytest.mark.timeout_seconds(_TEST_TIMEOUT_SECONDS)
def test_every_advertised_endpoint_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-03 / S-5: every advertised tool name round-trips through the real bridge.

    Coverage is computed from the complete ``tools/list`` payload — both
    canonical names and the strict-MCP ``mcp__ralph__<tool>`` aliases.
    For each canonical name with a SWEEP_CALLS entry we additionally
    issue the same call under the alias form when the alias is
    advertised. The bridge resolves the alias to its canonical handler
    via ``_resolve_alias_to_canonical``; if the alias is broken the
    dispatch falls through to the standard "Tool is not registered"
    error and the test fails closed.

    The ``declare_complete`` tool finalizes its own session (it writes
    a completion sentinel under ``.agent/`` and emits the
    ``[Completion event emitted to pipeline]`` marker). The sweep runs
    it as the terminal call against a freshly built server/session
    so a prior completion sentinel cannot poison the call or pollute
    later assertions.

    Web tools (``web_search`` / ``visit_url`` / ``download_url``) are
    exercised against mocked backends — the production handler
    dispatch chain is live; only the upstream boundary (HTTP fetch,
    search backend) is faked. See :func:`_install_web_backends`.

    The exec / unsafe_exec / raw_exec handlers are wrapped with an
    in-memory :class:`ExecRunDeps` runner so the sweep exercises the
    real bridge dispatch (capability check, parameter parsing,
    blacklist policy, output formatting, spill path) without ever
    spawning an OS process. See
    :func:`_patch_exec_handlers_with_in_memory_runner`.
    """
    _seed_workspace(tmp_path)
    _patch_exec_handlers_with_in_memory_runner(monkeypatch)
    _install_web_backends(monkeypatch)
    server, _ = _build_server(tmp_path)
    advertised = set(_drive_tools_list(server))

    # Shared sweep: every advertised canonical name with a SWEEP_CALLS entry.
    shared_targets = [
        (canonical_name, arguments)
        for canonical_name, arguments in SWEEP_CALLS.items()
        if canonical_name in advertised
    ]
    for canonical_name, arguments in shared_targets:
        response = _drive_call(server, canonical_name, arguments)
        _assert_call_round_trips(canonical_name, response)

    # Alias sweep: every advertised alias whose canonical name has a
    # SWEEP_CALLS entry. Alias dispatch resolves to the canonical
    # handler — coverage of the canonical handler was already proven
    # above; this step proves the alias emission and resolver.
    alias_targets = _alias_names_for_canonical(set(SWEEP_CALLS.keys()), advertised)
    for alias_name in alias_targets:
        canonical_name = alias_name[len("mcp__ralph__") :]
        arguments = SWEEP_CALLS[canonical_name]
        response = _drive_call(server, alias_name, arguments)
        _assert_call_round_trips(alias_name, response)

    # Terminal call: declare_complete on a freshly built server so the
    # completion sentinel does not leak into the shared server state.
    # The terminal call uses a distinct session_id and run_id from the
    # shared server so any sentinel written by this call is uniquely
    # attributable to this test run.
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
    """AC-03 contract: every advertised name is called, no coverage gap.

    Asserts the sweep-internal coverage map equals the full advertised
    tool set. The shared sweep covers every canonical name with a
    SWEEP_CALLS entry; the alias sweep covers every advertised
    ``mcp__ralph__<tool>`` whose canonical name is in SWEEP_CALLS; the
    terminal sweep covers ``declare_complete`` on a freshly built
    server. Together those must equal the advertised set — if a new
    tool lands in ``RalphToolName`` and gets registered with the
    production registry without a SWEEP_CALLS entry, this test fails
    closed rather than letting the gap drift silently into the
    default-gate verification contract.
    """
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
    """The well-formed error path for unknown tools is preserved.

    Negative-space contract: when an agent (or test) calls a tool that
    the bridge does not register, the response must be a JSON-RPC
    ``error`` envelope — never a silent success or a panic. The
    negative test guards against future regressions that accidentally
    swallow unknown-tool errors and would mask silent endpoint drift.
    """
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
