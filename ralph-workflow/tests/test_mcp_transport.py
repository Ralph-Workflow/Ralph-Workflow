"""Tests for MCP transport layer: StdioTransport and upstream clients."""

from __future__ import annotations

import contextlib
import io
import itertools
import sys
from io import BytesIO
from typing import IO

import pytest
from loguru import logger as loguru_logger

import ralph.process.manager as _mgr
from ralph.mcp.protocol.transport import StdioTransport
from ralph.mcp.upstream._stdio_upstream_client import _make_stdio_caller
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
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
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
    daemon_args: list[bool] = []
    seq: list[int] = [0]

    def fake_process_factory(command: list[str], cwd: str | None = None) -> _FakeProcess:
        created["command"] = command
        created["cwd"] = cwd
        return _FakeProcess()

    def fake_thread_factory(target: object, daemon: bool) -> _FakeThread:
        del target
        daemon_args.append(daemon)
        seq[0] += 1
        label = str(seq[0])
        events.append(f"create:{label}")
        return _FakeThread(
            label,
            on_start=lambda: events.append(f"start:{label}"),
            daemon=daemon,
        )

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
    # Resource-lifecycle precondition: BOTH reader and writer threads
    # MUST be requested with daemon=True so the ``# resource-lifecycle-ok:
    # bounded-daemon factory`` marker on ``_default_thread_factory`` holds.
    # If start() ever stops passing daemon=True, the audit's escape-hatch
    # would silently mask a real non-daemon thread leak.
    assert daemon_args == [True, True], (
        f"StdioTransport.start() must request daemon=True for both threads; "
        f"observed daemon args: {daemon_args}"
    )


@pytest.mark.asyncio
async def test_stdio_transport_close_joins_threads() -> None:
    """AC-03 regression: StdioTransport.close() joins reader/writer threads.

    The close() must call join(timeout=_CLOSE_THREAD_JOIN_SECONDS) on both
    the reader and writer thread doubles with a NON-None timeout. Black-box
    assertion on the INJECTED thread_factory doubles only: never reads
    production private attributes. The ``_FakeThread`` double
    (``tests/test_mcp_transport_helper__fakethread.py``) records every
    ``join(timeout=...)`` call so we can assert deterministically.

    Why this matters: a long-lived MCP client process that opens/closes
    transports in tight loops would otherwise leak dangling daemon threads,
    defeating the bounded-resource contract.
    """
    captured: list[_FakeThread] = []

    def fake_process_factory(command: list[str], cwd: str | None = None) -> _FakeProcess:
        del command, cwd
        return _FakeProcess()

    def fake_thread_factory(target: object, daemon: bool) -> _FakeThread:
        del target
        thread = _FakeThread(label=str(len(captured)), on_start=lambda: None, daemon=daemon)
        captured.append(thread)
        return thread

    transport = StdioTransport(
        ["python", "-m", "demo"],
        cwd="/tmp/demo",
        process_factory=fake_process_factory,
        thread_factory=fake_thread_factory,
    )
    transport.start()

    assert len(captured) == 2, "start() should create exactly 2 threads"

    await transport.close()

    # Black-box: assert against the captured thread_factory doubles only.
    # Both reader and writer MUST have been joined with a NON-None timeout.
    reader, writer = captured[0], captured[1]
    assert len(reader.join_calls) >= 1, "reader thread was never joined"
    assert len(writer.join_calls) >= 1, "writer thread was never joined"
    for recorded in reader.join_calls:
        assert recorded is not None, "reader join() called without a timeout"
        assert recorded > 0, f"reader join() called with non-positive timeout: {recorded}"
    for recorded in writer.join_calls:
        assert recorded is not None, "writer join() called without a timeout"
        assert recorded > 0, f"writer join() called with non-positive timeout: {recorded}"
    # Resource-lifecycle precondition: BOTH threads MUST have been requested
    # as daemon threads. This is the precondition for the
    # ``# resource-lifecycle-ok: bounded-daemon factory`` marker on
    # ``_default_thread_factory``. If start() ever stops passing daemon=True,
    # close()'s bounded join would no longer be the only safety net against
    # process-exit-blocking non-daemon threads.
    assert reader.daemon_arg is True, (
        f"reader thread MUST be requested with daemon=True; got {reader.daemon_arg!r}"
    )
    assert writer.daemon_arg is True, (
        f"writer thread MUST be requested with daemon=True; got {writer.daemon_arg!r}"
    )


@pytest.mark.asyncio
async def test_stdio_transport_close_warns_when_thread_still_alive() -> None:
    """AC-03 warning-path regression: StdioTransport.close() logs a warning when a
    reader/writer daemon thread is still alive after the bounded ``join()``.

    The plan explicitly required: "a still-alive thread logs a warning but
    does NOT raise". ``close()`` MUST observe ``is_alive()`` is True and log a
    warning, but MUST NOT raise — interpreter exit will still reap the daemon
    thread, so ``close()`` returning cleanly is the correct contract.

    Black-box assertion on the INJECTED thread_factory doubles only. The
    ``_FakeThread`` double is configured with ``alive_after_join=True`` to
    simulate a wedged reader/writer; ``is_alive_calls`` proves ``close()``
    consulted liveness. The loguru output is captured via a string sink so
    we can deterministically assert the warning text.
    """
    captured: list[_FakeThread] = []

    def fake_process_factory(command: list[str], cwd: str | None = None) -> _FakeProcess:
        del command, cwd
        return _FakeProcess()

    def fake_thread_factory(target: object, daemon: bool) -> _FakeThread:
        del target
        thread = _FakeThread(
            label=str(len(captured)),
            on_start=lambda: None,
            alive_after_join=True,
            daemon=daemon,
        )
        captured.append(thread)
        return thread

    transport = StdioTransport(
        ["python", "-m", "demo"],
        cwd="/tmp/demo",
        process_factory=fake_process_factory,
        thread_factory=fake_thread_factory,
    )
    transport.start()

    assert len(captured) == 2

    sink = io.StringIO()
    sink_id = loguru_logger.add(
        sink,
        format="{level.name}:{message}",
        level="WARNING",
        enqueue=False,
    )
    try:
        await transport.close()
    finally:
        loguru_logger.remove(sink_id)

    reader, writer = captured[0], captured[1]
    # Both threads were joined AND still alive — close() consulted is_alive() on each.
    assert reader.is_alive_calls >= 1, "close() never called is_alive() on the reader thread"
    assert writer.is_alive_calls >= 1, "close() never called is_alive() on the writer thread"
    # close() must not raise even when threads are wedged.
    # loguru text contains the WARNING-level message identifying each thread role.
    text = sink.getvalue()
    assert "WARNING" in text, f"no WARNING logged by close(); captured text: {text!r}"
    assert "_reader_thread" in text, (
        f"close() did not warn about the still-alive _reader_thread; captured: {text!r}"
    )
    assert "_writer_thread" in text, (
        f"close() did not warn about the still-alive _writer_thread; captured: {text!r}"
    )
    # Resource-lifecycle precondition: BOTH threads MUST have been requested
    # as daemon threads, even when they will eventually wedge (the daemon-ness
    # is the safety net that lets interpreter exit still reap them when the
    # bounded join returns with is_alive()=True).
    assert reader.daemon_arg is True, (
        f"reader thread MUST be requested with daemon=True; got {reader.daemon_arg!r}"
    )
    assert writer.daemon_arg is True, (
        f"writer thread MUST be requested with daemon=True; got {writer.daemon_arg!r}"
    )


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


class _FakeRaisingUpstreamPopen:
    """Fake Popen whose communicate() raises OSError to exercise the stdio caller cleanup path."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.wait_calls: list[float | None] = []
        self.stdin: IO[bytes] | None = None
        self.stdout: IO[bytes] | None = BytesIO()
        self.stderr: IO[bytes] | None = BytesIO()

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        self._returncode = 0
        return 0

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        raise OSError("broken pipe")

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


def test_stdio_caller_terminates_handle_on_communicate_exception() -> None:
    """Communicate OSError in the stdio caller must terminate the handle and propagate.

    Proves the new try/finally in _make_stdio_caller:
    - The underlying OSError is re-raised (caught and re-wrapped by StdioUpstreamClient).
    - The ProcessManager record reaches ProcessStatus.KILLED.
    - The fake Popen's terminate() is called exactly once.
    """
    fake = _FakeRaisingUpstreamPopen(pid=1)

    def factory(command: object, opts: object) -> object:
        del command, opts
        return fake

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=factory,
    )

    server = UpstreamMcpServer(
        name="test-upstream",
        transport="stdio",
        command="fake-cmd",
        args=(),
    )
    caller = _make_stdio_caller(server, pm=pm)
    client = StdioUpstreamClient(server, caller=caller)

    with pytest.raises(UpstreamCallError) as excinfo:
        client.list_tools()

    assert isinstance(excinfo.value.__cause__, OSError)
    assert str(excinfo.value.__cause__) == "broken pipe"

    records = pm.list_records(include_active=True, include_terminal=True)
    assert len(records) == 1
    assert records[0].status == ProcessStatus.KILLED
    assert fake.terminate_calls == 1
