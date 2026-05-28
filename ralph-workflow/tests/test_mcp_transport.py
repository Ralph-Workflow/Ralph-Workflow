"""Tests for MCP transport layer: StdioTransport and upstream clients."""

from __future__ import annotations

import contextlib
import itertools
import sys
from io import BytesIO
from typing import IO

import pytest

import ralph.process.manager as _mgr
from ralph.mcp.protocol.transport import StdioTransport
from ralph.mcp.upstream.client import HttpUpstreamClient, StdioUpstreamClient
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    get_process_manager,
    reset_process_manager,
)
from ralph.testing.fake_process import (
    make_async_process_factory,
    make_sync_process_factory,
)
from tests.test_mcp_transport_helper__fakethread import _FakeThread

PYTHON = sys.executable

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3, kill_followup_timeout_s=0.5, log_events=False
)


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin: IO[bytes] | None = BytesIO()
        self.stdout: IO[bytes] | None = BytesIO()
        self.stderr: IO[bytes] | None = BytesIO()

    def terminate(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int | None:
        return 0

    def kill(self) -> None:
        pass


def test_stdio_transport_uses_injected_process_and_thread_factories() -> None:
    """StdioTransport accepts custom process and thread factories for testing."""
    created: dict[str, object] = {}
    events: list[str] = []
    seq: list[int] = [0]

    def fake_process_factory(command: list[str], cwd: str | None = None) -> _FakeProcess:
        created["command"] = command
        created["cwd"] = cwd
        return _FakeProcess()

    def fake_thread_factory(target: object, daemon: bool) -> _FakeThread:
        seq[0] += 1
        label = str(seq[0])
        events.append(f"create:{label}")
        return _FakeThread(label, on_start=lambda: events.append(f"start:{label}"))

    transport = StdioTransport(
        ["python", "-m", "demo"],
        cwd="/tmp/demo",
        process_factory=fake_process_factory,
        thread_factory=fake_thread_factory,
    )

    transport.start()

    assert created["command"] == ["python", "-m", "demo"]
    assert created["cwd"] == "/tmp/demo"
    assert events == ["create:1", "create:2", "start:1", "start:2"]


@pytest.mark.asyncio
async def test_stdio_transport_default_factory_tracks_process_in_manager() -> None:
    """StdioTransport default factory registers the spawned process with ProcessManager.

    After start(), a record with label 'mcp-stdio:<cmd>' appears as RUNNING.
    After close(), the record transitions to a terminal state (EXITED or KILLED).

    Uses fake process factories to avoid real subprocess I/O.
    """
    reset_process_manager()
    try:
        # Build a ProcessManager with fake process factories
        sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
        async_factory = make_async_process_factory(itertools.count(1), returncode=0)
        pm = ProcessManager(
            policy=_FAST_POLICY,
            sync_process_factory=sync_factory,
            async_process_factory=async_factory,
        )

        # Replace singleton with our fake-injecting PM
        original = _mgr._pm_state.instance
        _mgr._pm_state.instance = pm

        try:
            transport = StdioTransport([PYTHON, "-c", "pass"])
            transport.start()

            # ProcessManager.spawn() registers the record synchronously before returning
            active = [r for r in pm.list_active() if r.label and r.label.startswith("mcp-stdio:")]

            assert len(active) == 1, f"Expected 1 mcp-stdio record, got {active}"
            assert active[0].status == ProcessStatus.RUNNING

            await transport.close()

            all_mcp = pm.list_records(include_terminal=True, label_prefix="mcp-stdio:")
            assert len(all_mcp) == 1
            assert all_mcp[0].status in (ProcessStatus.EXITED, ProcessStatus.KILLED)
        finally:
            _mgr._pm_state.instance = original
    finally:
        with contextlib.suppress(Exception):
            get_process_manager().shutdown_all(grace_period_s=0)
        reset_process_manager()


def test_http_upstream_client_lists_tools() -> None:
    server = UpstreamMcpServer(name="filesystem", transport="http", url="http://localhost:9999")

    def fake_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "tools/list"
        return {
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file from the server",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            ]
        }

    client = HttpUpstreamClient(server, caller=fake_caller)
    tools = client.list_tools()

    assert isinstance(tools[0], UpstreamTool)
    assert tools[0].name == "read_file"
    assert tools[0].description == "Read a file from the server"
    assert tools[0].input_schema == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }


def test_http_upstream_client_calls_tool() -> None:
    server = UpstreamMcpServer(name="filesystem", transport="http", url="http://localhost:9999")
    captured: dict[str, object] = {}

    def fake_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["params"] = params
        return {"content": [{"type": "text", "text": "hello world"}]}

    client = HttpUpstreamClient(server, caller=fake_caller)
    result = client.call_tool("read_file", {"path": "/tmp/hello.txt"})

    assert captured["method"] == "tools/call"
    assert captured["params"] == {
        "name": "read_file",
        "arguments": {"path": "/tmp/hello.txt"},
    }
    assert result == {"content": [{"type": "text", "text": "hello world"}]}


def test_http_upstream_client_raises_upstream_call_error_on_backend_failure() -> None:
    server = UpstreamMcpServer(name="filesystem", transport="http", url="http://localhost:9999")

    def failing_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        raise ConnectionRefusedError("connection refused")

    client = HttpUpstreamClient(server, caller=failing_caller)

    with pytest.raises(UpstreamCallError, match="filesystem"):
        client.call_tool("read_file", {"path": "/tmp/hello.txt"})


def test_stdio_upstream_client_lists_tools() -> None:
    server = UpstreamMcpServer(
        name="github",
        transport="stdio",
        command="npx",
        args=("@modelcontextprotocol/server-github",),
    )

    def fake_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "tools/list"
        return {
            "tools": [
                {
                    "name": "search_repos",
                    "description": "Search GitHub repos",
                    "inputSchema": {},
                },
                {
                    "name": "create_issue",
                    "description": "Create a GitHub issue",
                    "inputSchema": {},
                },
            ]
        }

    client = StdioUpstreamClient(server, caller=fake_caller)
    tools = client.list_tools()

    assert [t.name for t in tools] == ["search_repos", "create_issue"]
    assert all(isinstance(t, UpstreamTool) for t in tools)
