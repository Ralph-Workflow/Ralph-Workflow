from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from io import BytesIO
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

import pytest

from ralph.mcp.protocol.transport import StdioTransport
from ralph.mcp.upstream.client import HttpUpstreamClient, StdioUpstreamClient
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool
from ralph.process.manager import ProcessStatus, get_process_manager, reset_process_manager

PYTHON = sys.executable


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


class _FakeThread:
    def __init__(self, label: str, on_start: Callable[[], None]) -> None:
        self._label = label
        self._on_start = on_start

    def start(self) -> None:
        self._on_start()


def test_stdio_transport_uses_injected_process_and_thread_factories() -> None:
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


def test_stdio_transport_default_factory_tracks_process_in_manager() -> None:
    """StdioTransport default factory registers the spawned process with ProcessManager.

    After start(), a record with label 'mcp-stdio:<cmd>' appears as RUNNING.
    After close(), the record transitions to a terminal state (EXITED or KILLED).
    """
    reset_process_manager()
    try:
        pm = get_process_manager()
        transport = StdioTransport([PYTHON, "-c", "import time; time.sleep(30)"])
        transport.start()

        # Give the process a moment to register.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            active = [r for r in pm.list_active() if r.label and r.label.startswith("mcp-stdio:")]
            if active:
                break
            time.sleep(0.02)

        assert len(active) == 1, f"Expected 1 mcp-stdio record, got {active}"
        assert active[0].status == ProcessStatus.RUNNING

        asyncio.run(transport.close())

        all_mcp = [r for r in pm._records.values() if r.label and r.label.startswith("mcp-stdio:")]
        assert len(all_mcp) == 1
        assert all_mcp[0].status in (ProcessStatus.EXITED, ProcessStatus.KILLED)
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
                    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            ]
        }

    client = HttpUpstreamClient(server, caller=fake_caller)
    tools = client.list_tools()

    assert isinstance(tools[0], UpstreamTool)
    assert tools[0].name == "read_file"
    assert tools[0].description == "Read a file from the server"
    assert tools[0].input_schema == {"type": "object", "properties": {"path": {"type": "string"}}}


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
    assert captured["params"] == {"name": "read_file", "arguments": {"path": "/tmp/hello.txt"}}
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
                {"name": "search_repos", "description": "Search GitHub repos", "inputSchema": {}},
                {"name": "create_issue", "description": "Create a GitHub issue", "inputSchema": {}},
            ]
        }

    client = StdioUpstreamClient(server, caller=fake_caller)
    tools = client.list_tools()

    assert [t.name for t in tools] == ["search_repos", "create_issue"]
    assert all(isinstance(t, UpstreamTool) for t in tools)
