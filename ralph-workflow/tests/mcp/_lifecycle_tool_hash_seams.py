"""Shared test seams for tool-set hash, probe-classification and handover-step tests.

Private-by-convention scaffolding (fake processes, fake registries, the deterministic
synthetic private channel and its responder lifecycle) used by both
``test_lifecycle_tool_hash_and_classification.py`` and
``test_bridge_handover_step_errors.py``.
"""

import contextlib
import functools
import json
import pathlib
import socket
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from loguru import logger

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    create_handover_socketpair,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

#: Bound on the attach barrier — how long ``_handover_candidate`` waits for
#: the responder thread to take ownership of its socket end.
_RESPONDER_ATTACH_TIMEOUT_S: float = 5.0

#: Bound on the teardown join of a synthetic responder thread. The thread is
#: woken by the parent-end close that precedes the join, so this only bounds
#: how long teardown WAITS; it is never the reason a test passes.
_RESPONDER_JOIN_TIMEOUT_S: float = 5.0


class _FakeProcess:
    def __init__(self, poll_result: int | None = None, pid: int = 9999) -> None:
        self._poll_result = poll_result
        self._pid = pid
        self.terminated = False
        self.killed = False

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminated = True
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True
        self._poll_result = -9


def _make_inner(
    poll_result: int | None = None,
    pid: int = 9999,
    endpoint: str = "http://127.0.0.1:9100/mcp",
    session_file: pathlib.Path | None = None,
) -> StandaloneMcpProcess:
    if session_file is None:
        session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
        session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


class _FakeRegistry:
    """Registry double exposing the ``tools`` list the private channel reads."""

    def __init__(self, tools: list[dict[str, object]]) -> None:
        self.tools = tools


class _FakeMcpServer:
    """Minimal ``_registry`` holder shaped like the live MCP server."""

    def __init__(self, tools: list[dict[str, object]]) -> None:
        self._registry = _FakeRegistry(tools)


class _FakeListeningSocket:
    """Stand-in for the parent-owned listening socket (no real bind)."""

    def __init__(self) -> None:
        self.closed = False

    def fileno(self) -> int:
        return 7

    def close(self) -> None:
        self.closed = True


@contextlib.contextmanager
def _captured_logs() -> Iterator[list[str]]:
    """Collect loguru WARNING+ records emitted inside the block."""
    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        yield records
    finally:
        logger.remove(sink_id)


def _serve_tool_list(
    child_sock: socket.socket,
    *,
    pid: int,
    stage_id: str,
    tools: list[dict[str, object]],
    session_id: str = "",
    attached: threading.Event,
) -> None:
    """Answer one ``initialize`` + ``tools/list`` exchange on the private channel.

    ``attached`` is set the moment this thread owns ``child_sock`` and is about
    to read, which is the barrier ``_handover_candidate`` waits on before
    handing the caller a candidate. Without it the parent's request/response
    exchange races thread start-up and its success depends on the responder
    being scheduled inside the production readiness deadline
    (``_PRIVATE_READINESS_TIMEOUT_S``) — a wall-clock assumption whose
    satisfaction degrades under parallel host load, where a starved responder
    silently converts a drift refusal into a transport-timeout refusal.

    The thread's lifetime is bounded by the parent end being closed, which
    delivers EOF and ends the read loop; the ``synthetic_responders`` fixture
    guarantees that close happens at teardown.
    """
    with child_sock:
        attached.set()
        buf = b""
        while True:
            try:
                chunk = child_sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                msg = json.loads(line.decode("utf-8"))
                method = msg.get("method")
                if method == "initialize":
                    result: dict[str, object] = {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "ralph-mcp", "version": "0.1.0"},
                        "pid": pid,
                        "spawn_id": stage_id,
                        "session_id": session_id,
                        "broker_secret_fingerprint": None,
                    }
                elif method == "tools/list":
                    result = {"tools": tools}
                else:
                    result = {}
                resp = {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}
                child_sock.sendall(json.dumps(resp).encode("utf-8") + b"\n")


@dataclass
class _SyntheticResponder:
    """Test-owned teardown handle for one synthetic candidate's private channel.

    A synthetic responder thread that outlives its test is the load-sensitive
    hazard this type exists to remove. It parks in ``recv`` on a file-descriptor
    number; if it is still parked when a LATER test allocates its own
    ``socketpair``, the kernel hands out the same number, and the stale thread's
    eventual ``with child_sock`` close then closes the *later* test's socket.
    Whether that happens before or after the later test's exchange is pure
    thread-scheduling luck, and it gets worse the busier the host is.
    """

    parent_sock: socket.socket
    worker: threading.Thread

    def release(self) -> None:
        """Close the parent end, then join the responder. Teardown only.

        Never call this before the candidate has been verified:
        ``_verify_candidate`` skips the private-channel drift check for a
        candidate with no live ``handover_parent_sock`` and promotes it.
        """
        with contextlib.suppress(OSError):
            self.parent_sock.close()
        self.worker.join(timeout=_RESPONDER_JOIN_TIMEOUT_S)


def _release_all(responders: list[_SyntheticResponder]) -> None:
    for responder in responders:
        responder.release()


def register_synthetic_responders(request: pytest.FixtureRequest) -> list[_SyntheticResponder]:
    """Build the per-test responder registry and bind its teardown to the test.

    Each test module exposes it as a local ``synthetic_responders`` fixture because a
    fixture imported from another module is not discoverable by name.
    """
    responders: list[_SyntheticResponder] = []
    request.addfinalizer(functools.partial(_release_all, responders))
    return responders


def _handover_candidate(
    tmp_path: pathlib.Path,
    *,
    tools: list[dict[str, object]],
    pid: int,
    stage_id: str,
    label: str,
    responders: list[_SyntheticResponder],
) -> StandaloneMcpProcess:
    """Build a candidate whose private channel serves ``tools``.

    Returns only once the responder thread is provably attached, so the
    caller never starts the readiness exchange against an unscheduled
    responder. ``responders`` is the test-owned teardown registry supplied by
    the ``synthetic_responders`` fixture.
    """
    parent_sock, child_sock = create_handover_socketpair()
    candidate = _make_inner(
        poll_result=None,
        pid=pid,
        session_file=tmp_path / f"{label}-session.json",
    )
    candidate.handover_parent_sock = parent_sock
    candidate.stage_id = stage_id
    attached = threading.Event()
    worker = threading.Thread(
        target=_serve_tool_list,
        args=(child_sock,),
        kwargs={
            "pid": pid,
            "stage_id": stage_id,
            "tools": tools,
            "attached": attached,
        },
        daemon=True,
    )
    worker.start()
    if not attached.wait(timeout=_RESPONDER_ATTACH_TIMEOUT_S):
        raise AssertionError(
            f"synthetic candidate responder for stage {stage_id!r} never attached to its "
            f"private channel within {_RESPONDER_ATTACH_TIMEOUT_S}s"
        )
    responders.append(_SyntheticResponder(parent_sock=parent_sock, worker=worker))
    return candidate


def _bridge_with_old_child(
    tmp_path: pathlib.Path,
    *,
    old_pid: int = 1001,
    workspace_root: pathlib.Path | None = None,
    restart_fn: object = None,
    tool_entries_fn: object = None,
) -> lifecycle.RestartAwareMcpBridge:
    """Build a bridge whose old child is fake but lives under ``tmp_path``."""
    inner = _make_inner(
        poll_result=None,
        pid=old_pid,
        session_file=tmp_path / "old-session.json",
    )
    kwargs: dict[str, object] = {}
    if tool_entries_fn is not None:
        kwargs["tool_entries_fn"] = tool_entries_fn
    return lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn if restart_fn is not None else _make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        workspace_root=workspace_root,
        sleep_fn=lambda _seconds: None,
        **kwargs,
    )


TOOLS_BASE: list[dict[str, object]] = [
    {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
    {"name": "write_file", "description": "Write a file", "inputSchema": {"type": "object"}},
]
TOOLS_DRIFTED: list[dict[str, object]] = [
    {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
    {"name": "other_tool", "description": "Other", "inputSchema": {}},
]
