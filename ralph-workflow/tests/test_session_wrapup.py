"""MCP development-timebox warning and completion-admission regressions."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import DEV_WARN_EPOCH_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.coordination import ToolContent, ToolResult, handle_declare_complete
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import pytest


class _ReadHandler:
    def __call__(
        self, _session: object, _workspace: object, _params: dict[str, object]
    ) -> ToolResult:
        return ToolResult(content=[ToolContent.text_content("ok")], is_error=False)


def _server(tmp_path: Path, *, worker_namespace: Path | None = None) -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="read_file", description="Read", input_schema={"type": "object"}
            ),
            required_capability="workspace.read",
        ),
        _ReadHandler(),
    )
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="declare_complete", description="Complete", input_schema={"type": "object"}
            ),
            required_capability="artifact.submit",
        ),
        handle_declare_complete,
    )
    return McpServer(
        AgentSession(
            session_id="wrapup-session",
            run_id="wrapup-run",
            drain="development",
            capabilities={"ArtifactSubmit", "WorkspaceRead"},
            worker_namespace=worker_namespace,
        ),
        FsWorkspace(tmp_path),
        bridge,
    )


def _call(server: McpServer, name: str, msg_id: str = "call") -> list[dict[str, object]]:
    response, _ = server.handle_request(
        JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=msg_id,
            params={"name": name, "arguments": {}},
        ),
        ServerState.RUNNING,
    )
    assert response is not None and isinstance(response.result, dict)
    content = response.result["content"]
    assert isinstance(content, list)
    return [block for block in content if isinstance(block, dict)]


def _text(blocks: list[dict[str, object]]) -> str:
    return "\n".join(str(block.get("text", "")) for block in blocks)


def test_epoch_warning_appends_development_timebox_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path)
    assert "DEVELOPMENT-TIMEBOX WARNING" not in _text(_call(server, "read_file"))

    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(time.time() - 1.0))
    warning = _text(_call(server, "read_file", "warned"))
    assert "DEVELOPMENT-TIMEBOX WARNING" in warning
    assert "literally impossible" in warning
    assert "exhausted budget never qualify" in warning


def test_reset_preserves_epoch_warning_without_completion_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(time.time() - 1.0))
    monkeypatch.setattr(
        "ralph.mcp.tools.coordination._write_completion_sentinel", lambda *_args, **_kwargs: True
    )
    server = _server(tmp_path)

    assert "Task declared complete" in _text(_call(server, "declare_complete", "first"))
    server.reset_session_budget()
    assert "Task declared complete" in _text(_call(server, "declare_complete", "after-reset"))
    assert "DEVELOPMENT-TIMEBOX WARNING" in _text(_call(server, "read_file", "notice-after-reset"))


def test_reset_wrapup_notification_preserves_wire_compatibility(tmp_path: Path) -> None:
    server = _server(tmp_path)
    response, state = server.handle_request(
        JsonRpcRequest(
            jsonrpc="2.0", method="notifications/reset_wrapup", msg_id="reset", params={}
        ),
        ServerState.RUNNING,
    )
    assert response is None
    assert state is ServerState.RUNNING


# ---------------------------------------------------------------------------
# S-6: budget-aware development_wrapup_notice
# ---------------------------------------------------------------------------


def test_development_wrapup_notice_states_remaining_minutes_when_epochs_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-6: MCP deadline delivery preserves main dispatch and worker scope."""
    from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV

    now = time.time()
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(now - 600.0))
    monkeypatch.setenv(DEV_DEADLINE_EPOCH_ENV, repr(now + 1200.0))

    for worker_namespace in (None, tmp_path / "worker"):
        server = _server(tmp_path, worker_namespace=worker_namespace)
        notice = _text(_call(server, "read_file"))
        flat = " ".join(notice.split())
        assert "DEVELOPMENT-TIMEBOX WARNING" in notice
        assert "minutes remaining" in flat
        assert "submit the development result before the cut" in flat
        if worker_namespace is None:
            assert "independent ready group" in flat
        else:
            assert "independent ready group" not in flat
            assert "only your assigned work unit" in flat
            assert "Do not spawn sub-agents" in flat


def test_development_wrapup_notice_keeps_static_text_without_epochs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without published epochs, the notice keeps the existing static text
    (no remaining-minutes figure, no ready-group suggestion).
    """
    from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV
    from ralph.mcp.server._session_wrapup import development_wrapup_notice

    monkeypatch.delenv(DEV_WARN_EPOCH_ENV, raising=False)
    monkeypatch.delenv(DEV_DEADLINE_EPOCH_ENV, raising=False)

    notice = development_wrapup_notice()
    flat = " ".join(notice.split())

    assert "DEVELOPMENT-TIMEBOX WARNING" in notice
    assert "minutes remaining" not in flat
    assert "independent ready group" not in flat
    assert "literally impossible" in notice


def test_development_wrapup_notice_static_text_is_worker_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DA-027/WU-A: without published epochs, the static fallback
    notice is returned for both coordinator and worker roles. The
    static text is a single role-agnostic string so it MUST NOT
    direct any reader to dispatch sub-agents or fan out remaining
    work, regardless of which role invoked it. A worker render of
    the static fallback must still carry the partial-is-a-last-
    resort guidance, the worker-safe language, the
    difficulty/elapsed-time/exhausted-budget list, the truthful-
    evidence line, and the ``declare_complete`` close \u2014 without a
    dispatch directive. Without this regression defence a worker who
    races the warning point with no published deadline would still
    receive the dispatch instruction in its surface output,
    contradicting the role contract."""
    from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV, DEV_WARN_EPOCH_ENV
    from ralph.mcp.server._session_wrapup import development_wrapup_notice

    monkeypatch.delenv(DEV_WARN_EPOCH_ENV, raising=False)
    monkeypatch.delenv(DEV_DEADLINE_EPOCH_ENV, raising=False)

    notice = development_wrapup_notice(is_worker=True)
    flat = " ".join(notice.split())

    # The static fallback is the role-agnostic string regardless of
    # ``is_worker``. It must not branch on role \u2014 only the dynamic
    # branch does that.
    assert "minutes remaining" not in flat, (
        "worker static fallback must not advertise a remaining "
        "minutes figure when no deadline epoch was published"
    )
    assert "independent ready group" not in flat, (
        "worker static fallback must not mention an independent "
        "ready group"
    )
    # The static fallback must NOT direct any reader to dispatch.
    assert "dispatch it in parallel" not in flat, (
        "worker static fallback must not carry the dispatch directive"
    )
    assert "dispatch an independent ready group" not in flat, (
        "worker static fallback must not carry the ready-group "
        "dispatch directive"
    )
    # The canonical phrases that existing tests pin remain in the
    # static fallback so a worker who sees it without a published
    # deadline still has the partial-is-a-last-resort guidance,
    # the worker-safe language, the truthful-evidence line, and the
    # ``declare_complete`` close.
    assert "DEVELOPMENT-TIMEBOX WARNING" in notice
    assert "literally impossible" in notice
    assert "exhausted budget never qualify" in flat
    assert "use declare_complete" in flat
    assert "Workers" in flat and "never" in flat
    # The piecemeal handback warning is preserved as a guidance
    # phrase (not as a dispatch instruction).
    assert "piecemeal handbacks waste the cycle" in flat


# ---------------------------------------------------------------------------
# U-6: parallel-dispatch warning in the wrap-up notice's partial branch.
# ---------------------------------------------------------------------------


def test_development_wrapup_notice_coordinator_branch_warns_parallel_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """U-6/WU-A: the dynamic wrap-up notice's coordinator branch
    (``is_worker=False``) carries the parallel-dispatch warning inside
    its partial branch: remaining independent work should be
    dispatched in parallel rather than handed back piecemeal as
    partial; piecemeal handbacks waste the cycle. The worker branch
    has its own separate contract (see
    ``test_development_wrapup_notice_worker_branch_omits_parallel_dispatch``)."""
    from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV, DEV_WARN_EPOCH_ENV
    from ralph.mcp.server._session_wrapup import development_wrapup_notice

    now = time.time()
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(now - 600.0))
    monkeypatch.setenv(DEV_DEADLINE_EPOCH_ENV, repr(now + 1200.0))

    notice = development_wrapup_notice(is_worker=False)
    # The warning sits in the partial branch (the "Use partial only"
    # paragraph). Slice through the next sentence break so unrelated
    # guidance text does not mask a regression.
    partial_idx = notice.find("Use partial only")
    assert partial_idx >= 0, "partial branch missing for is_worker=False"
    end = notice.find("Difficulty,", partial_idx)
    partial_branch = notice[partial_idx:end] if end >= 0 else notice[partial_idx:]
    assert "dispatch it in parallel" in partial_branch, partial_branch
    assert "piecemeal handbacks waste the cycle" in partial_branch, partial_branch
    assert "each increment back as partial" in partial_branch, partial_branch


def test_development_wrapup_notice_worker_branch_omits_parallel_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WU-A: the dynamic wrap-up notice's worker branch
    (``is_worker=True``) MUST NOT carry a parallel-dispatch instruction.
    Workers do not dispatch sub-agents; the worker ``parallel_note``
    only mentions the worker-safe "if your unit is blocked, return
    truthful partial" instruction. The coordinator branch keeps the
    dispatch instruction; that is a separate contract."""
    from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV, DEV_WARN_EPOCH_ENV
    from ralph.mcp.server._session_wrapup import development_wrapup_notice

    now = time.time()
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(now - 600.0))
    monkeypatch.setenv(DEV_DEADLINE_EPOCH_ENV, repr(now + 1200.0))

    notice = development_wrapup_notice(is_worker=True)
    assert "dispatch it in parallel" not in notice, (
        "worker notice must not carry a parallel-dispatch instruction: "
        f"{notice!r}"
    )
    assert "dispatch an independent ready group" not in notice, (
        "worker notice must not carry a ready-group dispatch instruction: "
        f"{notice!r}"
    )
    # The worker-safe instruction is still present so a blocked worker
    # knows what to do.
    assert "If your unit is blocked, return truthful partial" in notice, notice
    # The worker-scope language survives.
    assert "Do not spawn sub-agents" in notice, notice
    assert "only your assigned work unit" in notice, notice


def test_development_wrapup_notice_coordinator_branch_uses_syntax_agnostic_ready_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WU-A: the dynamic coordinator branch must describe a ready
    group using plan-agnostic language. The previous wording pinned the
    ready-group definition to literal ``Depends on:`` / ``Directories:``
    / ``Paths:`` syntax, which is one convenient way to express the
    graph but not the only one. The new wording describes the ready
    group as units whose independence and pairwise-disjoint ownership
    are evident from the plan however the plan states it, while
    keeping the explicit "dispatch an independent ready group
    concurrently rather than trimming scope" instruction."""
    from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV, DEV_WARN_EPOCH_ENV
    from ralph.mcp.server._session_wrapup import development_wrapup_notice

    now = time.time()
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(now - 600.0))
    monkeypatch.setenv(DEV_DEADLINE_EPOCH_ENV, repr(now + 1200.0))

    notice = development_wrapup_notice(is_worker=False)
    flat = " ".join(notice.split())

    # The coordinator branch still carries a ready-group / dispatch
    # instruction, but the wording is syntax-agnostic.
    assert "independent ready group" in flat, (
        "coordinator branch must still carry a ready-group dispatch "
        "instruction"
    )
    # The literal syntax tokens are no longer required as the
    # definition; the wording now describes the group however the
    # plan states it.
    assert "Depends on:" not in flat, (
        "coordinator branch must not pin ready-group syntax to "
        "literal 'Depends on:'"
    )
    assert "Directories:" not in flat, (
        "coordinator branch must not pin ready-group syntax to "
        "literal 'Directories:'"
    )
    assert "Paths:" not in flat, (
        "coordinator branch must not pin ready-group syntax to "
        "literal 'Paths:'"
    )


def test_development_wrapup_notice_static_text_omits_dispatch_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WU-A: the static fallback notice (no epochs) is role-agnostic
    so it must NOT direct any reader to dispatch sub-agents or to
    fan out remaining work. The previous static text carried the
    same dispatch instruction as the dynamic coordinator branch,
    which leaked a main-session directive into a role-agnostic
    surface. The reworded static text keeps the partial-is-last-
    resort guidance, the difficulty/elapsed-time/exhausted-budget
    list, the "Never submit completed unless every reported item and
    piece of evidence is truthful" line, and the "use declare_complete"
    close — without a dispatch instruction."""
    from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV, DEV_WARN_EPOCH_ENV
    from ralph.mcp.server._session_wrapup import development_wrapup_notice

    monkeypatch.delenv(DEV_WARN_EPOCH_ENV, raising=False)
    monkeypatch.delenv(DEV_DEADLINE_EPOCH_ENV, raising=False)

    notice = development_wrapup_notice()
    flat = " ".join(notice.split())

    # The static text must NOT carry a dispatch instruction. It is
    # role-agnostic, so neither main-session nor worker readers
    # should be told to dispatch.
    assert "dispatch it in parallel" not in flat, (
        "static text must not carry 'dispatch it in parallel'; "
        "the static fallback is role-agnostic"
    )
    assert "dispatch an independent ready group" not in flat, (
        "static text must not carry a ready-group dispatch instruction"
    )
    # The canonical phrases that existing tests pin must still be
    # present in the static text.
    assert "DEVELOPMENT-TIMEBOX WARNING" in notice
    assert "literally impossible" in notice
    assert "exhausted budget never qualify" in flat
    assert "use declare_complete" in flat
    # The piecemeal handback warning is preserved as a guidance
    # phrase (not as a dispatch instruction).
    assert "piecemeal handbacks waste the cycle" in flat
    # Worker-safe language is preserved.
    assert "Workers" in flat and "never" in flat
