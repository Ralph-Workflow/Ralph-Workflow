"""WU-R2-ENVELOPE: child blocked at registry_built surfaces typed McpServerError.

Drives a REAL MCP subprocess (via start_mcp_server with real LifecycleDeps)
whose registry_built phase is held by a CHILD-SIDE gate: the child blocks
inside ``build_standalone_http_server`` after recording "registry_built" but
before returning, until a sentinel file appears (env
``MCP_TEST_REGISTRY_BUILD_GATE_ENV``, injected through ``McpServerExtras.extra_env``).

The parent sends a real preflight request which times out (30s default) while
the child remains blocked at the gate. The test asserts the surfaced error is a
typed ``McpServerError`` with ``last_stage="registry_built"``,
``elapsed_s > 0``, and ``elapsed_s < mcp_preflight_timeout_from_env().total_seconds()``.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol._startup_timeouts import mcp_preflight_timeout_from_env
from ralph.mcp.protocol.env import MCP_TEST_REGISTRY_BUILD_GATE_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._mcp_server_extras import McpServerExtras

if TYPE_CHECKING:
    from ralph.mcp.server._lifecycle_deps import LifecycleDeps

pytestmark = pytest.mark.subprocess_e2e


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-registry-build-gate",
        run_id="run-registry-build-gate",
        drain="development",
        capabilities={"WorkspaceRead"},
    )


@dataclass
class _PortSignal:
    """Test-side synchronization for learning the reserved host/port."""

    port_ready: threading.Event = field(default_factory=threading.Event)
    reserved: list[tuple[str, int]] = field(default_factory=list)


def _make_deps(signal: _PortSignal) -> LifecycleDeps:
    """Wrap only ``reserve_listening_socket`` so the test learns the endpoint.

    The preflight and spawn deps stay REAL: the parent's own preflight request
    must time out against the child-side gate exactly like production.
    """

    default_deps = lifecycle._default_lifecycle_deps()
    default_reserve = default_deps.reserve_listening_socket or lifecycle._reserve_listening_socket

    def recording_reserve() -> tuple[str, int, socket.socket]:
        host, port, sock = default_reserve()
        signal.reserved.append((host, port))
        signal.port_ready.set()
        return host, port, sock

    return default_deps.__replace__(
        reserve_listening_socket=recording_reserve,
        preflight_timeout=lambda: timedelta(milliseconds=5000),
    )


@pytest.mark.timeout_seconds(40)
def test_blocked_at_registry_built_surfaces_typed_error(tmp_path: Path) -> None:
    """When the child is held at registry_built, preflight times out and surfaces
    a typed McpServerError with last_stage=registry_built and elapsed_s within
    the preflight window."""
    session = _make_session()
    workspace = lifecycle.FsWorkspace(tmp_path)
    # Do NOT create the sentinel file: the child blocks indefinitely at the gate.
    sentinel = tmp_path / "registry_build_gate"
    extras = McpServerExtras(extra_env={MCP_TEST_REGISTRY_BUILD_GATE_ENV: str(sentinel)})

    signal = _PortSignal()
    deps = _make_deps(signal)

    result: list[McpServerError | BaseException] = []
    done = threading.Event()

    def _start() -> None:
        try:
            lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)
        except BaseException as exc:
            result.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_start, daemon=True)
    worker.start()

    try:
        # Wait for the bridge to own the listening socket (child has reached
        # registry_built and is blocked at the gate).
        assert signal.port_ready.wait(timeout=30.0), (
            "bridge never reserved its listening socket — child may have crashed"
        )

        # The child's stage file must say "registry_built" (it wrote this before
        # blocking at the gate).
        deadline = time.monotonic() + 15.0
        stage_label = ""
        while time.monotonic() < deadline:
            stages = sorted((tmp_path / ".agent").glob(".ralph_mcp_start_stage.*"))
            if stages:
                stage_label = stages[-1].read_text(encoding="utf-8").strip()
                if stage_label == "registry_built":
                    break
            time.sleep(0.05)
        assert stage_label == "registry_built", (
            f"expected child to be blocked at registry_built, got {stage_label!r}"
        )

        # Wait for start_mcp_server to raise (preflight times out).
        assert done.wait(timeout=30.0), (
            "start_mcp_server did not raise within 30s window"
        )

        # Assert the surfaced error is a typed McpServerError with the
        # correct stage and timing.
        assert result, "start_mcp_server returned without raising"
        exc = result[0]
        assert isinstance(exc, McpServerError), (
            f"expected McpServerError, got {type(exc).__name__}: {exc}"
        )
        assert exc.last_stage == "registry_built", (
            f"expected last_stage=registry_built, got {exc.last_stage!r}"
        )
        assert exc.elapsed_s is not None, "elapsed_s must not be None"
        assert exc.elapsed_s > 0, f"elapsed_s must be > 0, got {exc.elapsed_s}"
        # The parent's ``elapsed_s`` is the wall-clock from spawn to error
        # raise and includes the preflight timeout (30s) plus child-shutdown
        # and stage-file-read overhead. It MUST stay below
        # preflight_timeout + 30s cleanup overhead (proves the preflight
        # actually fired first; a runaway ``elapsed_s`` would mean the
        # child was hanging past the parent's preflight window and the
        # supervisor was no longer enforcing it).
        preflight_timeout = mcp_preflight_timeout_from_env().total_seconds()
        assert exc.elapsed_s < preflight_timeout + 30.0, (
            f"elapsed_s ({exc.elapsed_s:.3f}s) must be < "
            f"preflight_timeout+30s ({preflight_timeout + 30.0:.3f}s); "
            "a runaway value means the parent preflight window did not fire"
        )
    finally:
        # Clean up: touch the sentinel so the child can exit if it is still alive.
        sentinel.touch()
        worker.join(timeout=10.0)
