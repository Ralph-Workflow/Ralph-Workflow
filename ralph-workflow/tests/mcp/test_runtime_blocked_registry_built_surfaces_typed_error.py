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
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

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


@pytest.mark.timeout_seconds(15)
def test_blocked_at_registry_built_surfaces_typed_error(tmp_path: Path) -> None:
    """When the child is held at registry_built, preflight times out and surfaces
    a typed McpServerError with last_stage=registry_built and elapsed_s within
    the preflight window."""
    session = _make_session()
    workspace = lifecycle.FsWorkspace(tmp_path)
    # Do NOT create the sentinel file: the child blocks indefinitely at the gate.
    sentinel = tmp_path / "registry_build_gate"
    # 5s preflight is the minimum that satisfies the import-time
    # invariant: preflight * _PROBE_BUDGET_SHARE must STRICTLY exceed
    # _PROBE_SAFETY_S (2s). 4s yields 2.0s ceiling which fails the
    # ``<= _PROBE_SAFETY_S`` check. 5s yields 2.5s ceiling.
    # The child reaches the gate in well under 1s, the gate's
    # hard deadline is 0.5s, and the parent fires at 5s. Test
    # wall-clock: ~0.7s (gate fires first, child exits, parent
    # surfaces the typed error).
    preflight_timeout_ms = 5000
    extras = McpServerExtras(
        extra_env={
            MCP_TEST_REGISTRY_BUILD_GATE_ENV: str(sentinel),
            "RALPH_MCP_PREFLIGHT_TIMEOUT_MS": str(preflight_timeout_ms),
        }
    )

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
        assert signal.port_ready.wait(timeout=10.0), (
            "bridge never reserved its listening socket — child may have crashed"
        )

        # Wait for start_mcp_server to raise (child-side budget exceeded or preflight fails).
        assert done.wait(timeout=10.0), "start_mcp_server did not raise within 10s window"

        # The child's stage file must say "registry_built".
        stage_label = ""
        stages = sorted((tmp_path / ".agent").glob(".ralph_mcp_start_stage.*"))
        if stages:
            stage_label = stages[-1].read_text(encoding="utf-8").strip()
        assert stage_label == "registry_built", (
            f"expected child stage file to record registry_built, got {stage_label!r}"
        )

        # The child's live server log must also record the registry_built stage.
        log_path = lifecycle.mcp_server_log_path(tmp_path)
        assert log_path.exists(), f"child server log does not exist at {log_path}"
        log_content = log_path.read_text(encoding="utf-8")
        assert "registry_built" in log_content, (
            f"expected 'registry_built' in child server log ({log_path}), got:\n{log_content}"
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

        # The parent's preflight fires strictly before the preflight timeout.
        preflight_timeout_s = preflight_timeout_ms / 1000.0
        assert exc.elapsed_s < preflight_timeout_s, (
            f"elapsed_s ({exc.elapsed_s:.3f}s) must be < "
            f"configured preflight deadline ({preflight_timeout_s:.3f}s); "
            "proves child failed fast within its configured budget rather than waiting for preflight timeout"
        )
    finally:
        # Clean up: touch the sentinel so the child can exit if it is still alive.
        sentinel.touch()
        worker.join(timeout=10.0)
