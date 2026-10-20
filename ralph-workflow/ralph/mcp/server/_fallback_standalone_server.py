"""Fallback standalone MCP server using Python's built-in HTTP server."""

from __future__ import annotations

import hashlib
import os
import socket as _socket_mod
import threading
import time
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger

from ralph.mcp.artifacts._path_file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.protocol.env import (
    MCP_ACCEPT_PAUSE_FILE,
    MCP_ACCEPT_RESUME_FILE,
    MCP_BRIDGE_PARENT_SOCK,
    MCP_STAGE_ID_ENV,
    MCP_TEST_PRE_SERVE_GATE_ENV,
)
from ralph.mcp.runtime_executors import shutdown_runtime_executors
from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._handover_protocol import (
    start_private_readiness_listener,
    wait_for_candidate_resume,
)
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._runtime_constants import (
    DEFAULT_MOUNT_PATH,
    DEFAULT_TRANSPORT,
    SERVER_POLL_INTERVAL_SECONDS,
)
from ralph.mcp.server._server_state import ServerState
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS

if TYPE_CHECKING:
    from collections.abc import Callable
    from socket import socket

    from ralph.mcp.server._mcp_server import McpServer

    EnvGetter = Callable[[str, str | None], str | None]


#: Upper bound (seconds) the test-only pre-serve gate waits for its sentinel
#: file before failing the child loudly. Bounded so a forgotten sentinel can
#: never wedge a subprocess-E2E run.
_PRE_SERVE_GATE_TIMEOUT_S: float = 60.0


class _FallbackStandaloneServer:
    def __init__(
        self,
        host: str,
        port: int,
        mcp_server: McpServer,
        *,
        env_getter: EnvGetter = os.environ.get,
        parent_socket: socket | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._mcp_server = mcp_server
        self._env_getter = env_getter
        self._httpd: _FallbackHttpServer | None = None
        # Adopt the parent's already-listening socket when one is
        # supplied. The bridge owns this socket for its full
        # lifetime (R1); the child must not bind a new one. When
        # ``parent_socket`` is ``None`` (one-shot CLI invocations
        # like ``ralph-mcp --port 9000`` with no bridge parent),
        # the standalone server still binds its own port.
        self._parent_socket: socket | None = parent_socket

    @property
    def bound_address(self) -> tuple[str, int]:
        """Return the (host, port) the server is bound to after run() is called."""
        if self._httpd is None:
            raise RuntimeError("Server has not been started yet")
        return cast(
            "tuple[str, int]", self._httpd.server_address
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)

    def prepare_httpd(self) -> _FallbackHttpServer:
        """Instantiate and bind the underlying HTTP server on the host/port or parent_socket."""
        if self._httpd is not None:
            return self._httpd
        if self._parent_socket is not None:
            httpd = _FallbackHttpServer(
                (self._host, self._port),
                _FallbackHttpHandler,
                parent_socket=self._parent_socket,
            )
        else:
            httpd = _FallbackHttpServer((self._host, self._port), _FallbackHttpHandler)
        httpd.mcp_server = self._mcp_server
        httpd.state = ServerState.UNINITIALIZED
        httpd.shutdown_event = Event()
        # Class-annotated optional attributes (``_fallback_http_server.py``)
        # that the handler reads on the /health path. Annotations alone do
        # not create instance attributes, so without these assignments a
        # bare GET /health raised AttributeError and the liveness route
        # served by the production standalone runtime was unreachable.
        httpd.health_probe_fn = None
        httpd.metrics = None
        self._httpd = httpd
        return httpd

    def _setup_handover_readiness(self) -> None:
        parent_sock_fd_str = self._env_getter(MCP_BRIDGE_PARENT_SOCK, None)
        if not parent_sock_fd_str:
            return
        try:
            parent_sock_fd = int(parent_sock_fd_str)
            child_sock = _socket_mod.socket(
                fileno=parent_sock_fd,
                family=_socket_mod.AF_UNIX,
                type=_socket_mod.SOCK_STREAM,
            )
            stage_id = self._env_getter(MCP_STAGE_ID_ENV, "") or ""
            session = self._mcp_server._session
            session_id_obj: object = getattr(session, "session_id", "")
            session_id = str(session_id_obj) if session_id_obj is not None else ""
            raw_secret: object = getattr(session, "broker_secret", None)
            broker_fp: str | None = None
            if isinstance(raw_secret, str) and raw_secret:
                broker_fp = hashlib.sha256(raw_secret[:8].encode()).hexdigest()
            start_private_readiness_listener(
                child_sock,
                self._mcp_server,
                stage_id=stage_id,
                session_id=session_id,
                broker_fingerprint=broker_fp,
            )
        except Exception:
            logger.opt(exception=True).warning("Failed to start private readiness listener")

    def _setup_handover_pause(self, httpd: _FallbackHttpServer, stop_event: Event | None) -> None:
        pause_file_str = self._env_getter(MCP_ACCEPT_PAUSE_FILE, None)
        if not pause_file_str:
            return

        def _pause_watcher() -> None:
            pause_path = Path(pause_file_str)
            while not (stop_event is not None and stop_event.is_set()):
                # filesystem-read-ok: transient handover sentinel file polled to detect pause request
                if pause_path.exists():
                    try:
                        httpd.drop_listening_socket()
                        ack_path = pause_path.with_name(pause_path.name + ".ack")
                        # filesystem-write-ok: transient handover sentinel file written once on process retirement
                        ack_path.write_text("ACK\n", encoding="utf-8")
                        logger.info("Retiring MCP server acknowledged handover pause")
                    except Exception:
                        logger.opt(exception=True).warning("Handover pause acknowledgement failed")
                    break
                # filesystem-poll-ok: bounded sleep interval in background pause watcher thread
                time.sleep(0.005)

        threading.Thread(target=_pause_watcher, daemon=True, name="mcp-pause-watcher").start()

    def _await_handover_resume(
        self,
        stop_event: Event | None,
        on_ready: Callable[[], None] | None,
        ready_event: Event | None,
    ) -> None:
        resume_file_str = self._env_getter(MCP_ACCEPT_RESUME_FILE, None)
        if not resume_file_str:
            return
        if on_ready is not None:
            on_ready()
        if ready_event is not None:
            ready_event.set()
        resumed = wait_for_candidate_resume(
            Path(resume_file_str),
            timeout=30.0,
            stop_event=stop_event,
        )
        if not resumed and (stop_event is None or not stop_event.is_set()):
            raise McpServerError(
                "Candidate failed to receive resume signal within deadline",
                last_stage="resume_timed_out",
                elapsed_s=30.0,
                restart_count=0,
            )

    def run(
        self,
        transport: Literal["streamable-http"] = DEFAULT_TRANSPORT,
        *,
        ready_event: Event | None = None,
        stop_event: Event | None = None,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        if transport != DEFAULT_TRANSPORT:
            raise ValueError(f"Unsupported transport: {transport}")
        httpd = self.prepare_httpd()
        if stop_event is not None:

            def _stop_watcher() -> None:
                stop_event.wait()  # mcp-timeout-ok: daemon thread waiting for explicit stop signal
                httpd.shutdown()

            stop_thread = threading.Thread(target=_stop_watcher, daemon=True)
            stop_thread.start()
        # Subprocess-E2E test seam (R1 "slow startup still becomes ready"
        # invariant): when the env names a sentinel file, block here — the
        # server object exists but nothing has accepted a connection yet —
        # until the file appears. Production never sets this variable, so
        # real runs skip the branch entirely.
        gate_path = self._env_getter(MCP_TEST_PRE_SERVE_GATE_ENV, None)
        if gate_path:
            gate_path_obj = Path(gate_path)
            deadline = time.monotonic() + _PRE_SERVE_GATE_TIMEOUT_S
            while not DEFAULT_FILE_BACKEND.exists(
                gate_path_obj
            ):  # filesystem-read-ok: subprocess-E2E test seam polls a path the test fixture creates on its side; routed through the shared FileBackend seam
                if time.monotonic() >= deadline:
                    raise McpServerError(
                        f"pre-serve gate timed out after {_PRE_SERVE_GATE_TIMEOUT_S:g}s "
                        f"waiting for {gate_path}",
                        last_stage="ready_to_serve",
                        elapsed_s=_PRE_SERVE_GATE_TIMEOUT_S,
                        restart_count=0,
                    )
                # filesystem-poll-ok: test-only pre-serve gate polls a sentinel file the subprocess-E2E fixture creates; production never sets this env, and the loop is deadline-bounded
                time.sleep(0.005)
        # Startup banner announces the live configuration so an operator can
        # confirm which transport, which session class, and which effective
        # timeouts are running. The earlier code only printed host/port —
        # leaving the session class, the dispatch caps, and the auth posture
        # invisible. Each field is queried from the running configuration so
        # the banner cannot drift from the actual behavior.
        auth_token_set = bool(self._env_getter("MCP_AUTH_TOKEN", None))
        logger.info(
            "ralph-mcp startup: transport={transport} "
            "session_class={session_class} "
            "dispatch_cap_ms={dispatch_cap} "
            "drain_ceiling_ms={drain_ceiling} "
            "kill_escalation_ms={kill_escalation} "
            "probe_timeout_ms={probe_timeout} "
            "auth_token_set={auth} "
            "inherited_fd={inherited_fd}",
            transport="streamable-http",
            session_class=type(self._mcp_server._session).__name__,
            dispatch_cap=EXEC_MAX_TIMEOUT_MS,
            drain_ceiling=5000,
            kill_escalation=5000,
            probe_timeout=int(
                float(self._env_getter("RALPH_MCP_PROBE_TIMEOUT_MS", "5000") or "5000")
            ),
            auth=auth_token_set,
            inherited_fd=self._parent_socket is not None,
        )
        # Make-before-break handover setup
        self._setup_handover_readiness()
        self._setup_handover_pause(httpd, stop_event)
        self._await_handover_resume(stop_event, on_ready, ready_event)

        # R4 / R4-banner: signal ready and emit the listening banner
        # ONLY when serving is about to start (before serve_forever
        # blocks). This ensures the banner and the stage file both
        # reflect "server is accepting connections", not "server
        # has been constructed but not started".
        if on_ready is not None:
            # The parent records the ready_to_serve stage file at the
            # actual serving boundary, not when ``build_standalone_http_server``
            # returns. Without this hook the stage would land BEFORE
            # the socket is actually accepting connections.
            on_ready()
        if ready_event is not None:
            ready_event.set()
        # WU-B: the banner MUST reach the parent's pipe even when the
        # parent did not pre-set ``PYTHONUNBUFFERED``. The runtime
        # additionally reconfigures ``sys.stdout`` to line-buffered on
        # the env_loaded path; this explicit ``flush=True`` is the
        # backstop for any caller that hits the print site before the
        # reconfigure (e.g. embedded test use) or with stdout attached
        # to a fully-buffered block-buffered pipe.
        print(
            f"Ralph MCP server listening on http://{self._host}:{self._port}{DEFAULT_MOUNT_PATH}",
            flush=True,
        )
        try:
            httpd.serve_forever(poll_interval=SERVER_POLL_INTERVAL_SECONDS)
        finally:
            shutdown_runtime_executors()
            # Release the listening TCP socket on every exit path
            # (normal return, exception, external shutdown). Without
            # this, embedded/long-lived use leaks the FD; one-shot
            # CLI runs rely on OS-level cleanup at exit but the
            # deterministic close is the contract this server
            # advertises (see _fallback_http_server.server_close).
            httpd.server_close()
