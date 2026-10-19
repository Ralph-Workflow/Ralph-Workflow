"""MCP server lifecycle helpers using a standalone localhost HTTP process."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast

from loguru import logger

from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    payload_transport,
    profile_for_caller,
    resolve_capability_profile,
)
from ralph.mcp.protocol._preflight_error import PreflightError
from ralph.mcp.protocol._session_bridge_like import SessionBridgeLike
from ralph.mcp.protocol._startup_timeouts import mcp_whole_startup_budget_from_env
from ralph.mcp.protocol.env import (
    MCP_INHERITED_FD_ENV,
    MCP_OWNER_PID_ENV,
    MCP_RESPAWN_ENV,
    MCP_STAGE_ID_ENV,
)
from ralph.mcp.protocol.env import MCP_SESSION_FILE_ENV as SESSION_FILE_ENV
from ralph.mcp.protocol.startup import (
    mcp_preflight_timeout_from_env,
    mcp_probe_timeout_from_env,
    preflight_http_mcp_server_tools,
    probe_mcp_http_endpoint,
)
from ralph.mcp.server._http_tools_list import (
    _LIFECYCLE_MAX_RESPONSE_BYTES,
    _http_tools_list_names,
)
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._mcp_health_event import (
    _DURABLE_SINK_LOCK,
    RestartReason,
    current_host_is_contended,
    get_durable_sink_path,
    pop_recent_host_contention_signals,
    record_host_contention_signal,
)
from ralph.mcp.server._mcp_health_event import (
    configure_bridge_sink as _configure_bridge_sink,
)
from ralph.mcp.server._mcp_health_event import (
    record_restart_decision as _record_restart_decision,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._mcp_server_extras import McpServerExtras
from ralph.mcp.server._process_like import ProcessLike
from ralph.mcp.server._reserved_socket import _reserve_listening_socket
from ralph.mcp.server._server_log import (
    mcp_server_log_path,
    mcp_server_log_size,
    read_mcp_server_log_since,
)
from ralph.mcp.server._snapshots import (
    _finger,
    snapshot_dir,
    write_snapshot,
)
from ralph.mcp.server._spawn_process import SpawnProcess
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server._start_stage_tracker import read_last_stage as _read_start_stage
from ralph.mcp.tool_contract import visible_owned_tool_names
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.tool_catalog_cache import (
    UpstreamToolCatalogSnapshot,
    apply_tool_catalog_env,
    get_tool_catalog_snapshot,
)
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager
from ralph.workspace.fs import FsWorkspace

#: Env var carrying the session_id for snapshot-based persistence across respawns.
MCP_SESSION_ID_ENV = "MCP_SESSION_ID"

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from socket import socket as _socket_type

    from ralph.mcp.protocol.startup import SessionLike, WorkspaceLike
    from ralph.mcp.server._activity_relay import ActivityRelay
    from ralph.mcp.upstream.registry import UpstreamRegistry

# Additive cap on the tool-registry-reset counter. The recovery
# controller's `max_recovery_attempts` and the MCP restart policy's
# `max_restarts` are independent. This cap bounds the third retry
# surface — when a tool-availability failure recurs after the bridge
# has rebuilt its visible tool list, the orchestrator needs a
# distinguishable cap error so the operator can diagnose which bound
# fired. The error message MUST contain the substring
# 'tool-registry-reset exhausted' so the orchestrator can branch on it.
_TOOL_REGISTRY_MAX_RESETS: int = 3
# Hard cap on the exponent for the sustained-contention probe-timeout
# backoff. A live child kept deferring past this cap would otherwise
# raise ``OverflowError`` from ``timedelta * 2**N`` and tear down the
# health-check path — the exact incident class. The cap is far past
# the realistic ceiling (the timeout is min'd against the preflight
# ceiling below, so any exponent above ~10 already saturates the
# observable probe timeout). 32 keeps 2**N < 2**32 = 4.3e9, well
# below the timedelta microsecond range.
_PROBE_TIMEOUT_EXPAWN_CAP: int = 32


def _restart_error_fields(exc: BaseException) -> tuple[str | None, float | None]:
    """Checked boundary: pull optional restart diagnostics off a foreign exception."""
    last_stage: object = getattr(exc, "last_stage", None)
    elapsed_s: object = getattr(exc, "elapsed_s", None)
    return (
        last_stage if isinstance(last_stage, str) else None,
        elapsed_s if isinstance(elapsed_s, float) and not isinstance(elapsed_s, bool) else None,
    )


if not _TOOL_REGISTRY_MAX_RESETS > 0:
    raise RuntimeError(
        f"_TOOL_REGISTRY_MAX_RESETS must be positive (got {_TOOL_REGISTRY_MAX_RESETS})"
    )

# Alias-verify timeout for the post-respawn tools/list probe. The
# probe is HTTP-based and bounded so a wedged inner process does
# not stall the recovery path. The invariant below is enforced at
# import time via `if`/`raise RuntimeError` (NOT `assert`) so it
# survives `python -O`.
_RESPAWN_ALIAS_VERIFY_TIMEOUT_S: float = 2.0
_RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S: float = 30.0
if not (
    _RESPAWN_ALIAS_VERIFY_TIMEOUT_S > 0
    and _RESPAWN_ALIAS_VERIFY_TIMEOUT_S < _RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S
):
    raise RuntimeError(
        "_RESPAWN_ALIAS_VERIFY_TIMEOUT_S must be in (0,"
        f" {_RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S:g}) seconds"
        f" (got {_RESPAWN_ALIAS_VERIFY_TIMEOUT_S})"
    )

# Reset-wrapup timeout for the per-attempt session-budget reset POST.
# Same bounded, fail-closed contract as the alias-verify timeout so
# ``audit_mcp_timeout`` stays green: a wedged inner process or a
# just-respawned one that has not finished binding its HTTP socket
# must not stall the attempt boundary. The invariant below is
# enforced at import time via `if`/`raise RuntimeError` (NOT
# `assert`) so it survives `python -O`.
_RESET_WRAPUP_TIMEOUT_S: float = 2.0
if not _RESET_WRAPUP_TIMEOUT_S > 0:
    raise RuntimeError(f"_RESET_WRAPUP_TIMEOUT_S must be positive (got {_RESET_WRAPUP_TIMEOUT_S})")

#: Probe-overrun slack (seconds). A probe that exceeds its deadline by more
#: than this is classified as host contention (C3) rather than server death.
_PROBE_DEADLINE_SLACK_S: float = 0.5
#: Hard ceiling on consecutive probe-failure deferrals. When the deferral
#: counter reaches this the bridge forces a restart regardless of whether
#: a fresh contention signal is present, preventing indefinite deferral of
#: a genuinely wedged child.
_DEFERRAL_COUNT_CEILING: int = 30
#: Wall-time ceiling on the accumulated probe deferral time. A probe that
#: overruns by more than this in aggregate (even if individual overruns
#: are small) is treated as sustained contention and forces replacement.
_DEFERRAL_WALL_TIME_CEILING_S: float = 60.0
if not (
    _PROBE_DEADLINE_SLACK_S > 0
    and _DEFERRAL_COUNT_CEILING > 0
    and _DEFERRAL_WALL_TIME_CEILING_S > 0
):
    raise RuntimeError(
        f"_PROBE_DEADLINE_SLACK_S={_PROBE_DEADLINE_SLACK_S} "
        f"_DEFERRAL_COUNT_CEILING={_DEFERRAL_COUNT_CEILING} "
        f"_DEFERRAL_WALL_TIME_CEILING_S={_DEFERRAL_WALL_TIME_CEILING_S} must all be positive"
    )

#: Hard upper bound (bytes) on a single ``response.read()`` call in this
#: module. Both the reset_session_budget path (which discards the body) and
#: the ``_http_tools_list_names`` path (which parses the body as JSON) cap
#: the read at this value so a misbehaving upstream that streams an
#: unbounded response body cannot OOM the parent. 1 MiB is well above any
#: realistic ``tools/list`` JSON payload and matches the size budget an
#: operator would expect for a discardable wrap-up response (AC-08).

# Canonical tool name whose alias is verified after every
# ``reset_tool_registry()`` call. ``read_file`` is the most common
# wedge surface: a regression that strips the alias from the
# post-respawn tools/list re-introduces the post-tool-result wedge
# even though ``reset_tool_registry()`` returned successfully.
_ALIAS_VERIFY_TOOL_NAME: str = "read_file"
# MCP server name used to build the alias. The companion constant
# lives in ``ralph.mcp.tools.names``; we hard-code ``"ralph"`` here
# to keep the lifecycle module import-light and avoid a circular
# dependency through ``ralph.mcp.tools.bridge``.
_ALIAS_VERIFY_SERVER_NAME: str = RALPH_MCP_SERVER_NAME

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.startup import SessionLike, WorkspaceLike
    from ralph.mcp.upstream.registry import UpstreamRegistry

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

#: Consecutive failed probes required before an otherwise-running MCP server
#: is restarted. A single bounded probe window is not evidence of death: on a
#: loaded machine a healthy server misses one routinely, and restarting on the
#: first miss tore down working servers in the middle of agent calls.
_PROBE_FAILURES_BEFORE_RESTART = 3

#: Filename of the durable JSONL sink for restart decisions, stored under
#: ``<workspace>/.agent/`` so it is workspace-local and bounded by the
#: bridge lifecycle.  "ralph_mcp_restart_decisions" is distinctive enough
#: to identify the file in incident triage without polluting the root.
_DURABLE_SINK_FILENAME = "ralph_mcp_restart_decisions.jsonl"


def _has_stage(exc: BaseException) -> bool:
    """Return True when ``exc`` carries the supervisor ``last_stage`` attribute.

    Runtime predicate that gates the typed ``last_stage`` / ``elapsed_s``
    read in :func:`_optional_attr_str` and :func:`_optional_attr_float`.
    The restart loop catches a generic :class:`BaseException`; only
    subclasses that declare the supervisor contract (in practice
    :class:`McpServerError`) contribute values to the rewrap.
    """
    return hasattr(exc, "last_stage") and hasattr(exc, "elapsed_s")


def _read_supervisor_field(exc: BaseException, name: str) -> object:
    """Read a single attribute from ``exc.__dict__`` as ``object``.

    The returned value is intentionally typed as :class:`object`: it is
    an untyped read that the caller narrows with ``isinstance``. The
    helper exists so the ``__dict__`` access (which mypy types as
    ``dict[str, Any]``) lives in one place, gated by the ``_has_stage``
    runtime predicate, instead of being open-coded at every rewrap call
    site. Returning :class:`object` keeps the expression typed and
    avoids ``disallow_any_expr`` violations at the call site.
    """
    storage: dict[str, object] = exc.__dict__
    return storage[name]


def _optional_attr_str(exc: BaseException) -> str | None:
    """Return ``exc.last_stage`` if it is a non-empty :class:`str`, else ``None``.

    Typed boundary helper for the supervisor's exception-rewrap path.
    The ``_has_stage`` gate confines the dynamic attribute read to a
    narrow surface: any exception that does not declare the supervisor
    contract short-circuits to ``None``. The read goes through
    :func:`_read_supervisor_field` so the only ``Any``-typed value
    lives behind a single typed boundary, and ``isinstance(raw, str)``
    narrows to a typed :class:`str` at the call site without an
    unchecked downcast.
    """
    if not _has_stage(exc):
        return None
    raw = _read_supervisor_field(exc, "last_stage")
    if not isinstance(raw, str) or not raw:
        return None
    return raw


def _optional_attr_float(exc: BaseException) -> float | None:
    """Return ``exc.elapsed_s`` if it is a non-bool numeric, else ``None``.

    Mirror of :func:`_optional_attr_str` for the elapsed-time field. The
    ``isinstance`` check accepts both :class:`float` and :class:`int` so
    a JSON-derived numeric that arrives as ``int`` (whole seconds) is
    not silently dropped at the typed boundary.
    """
    if not _has_stage(exc):
        return None
    raw = _read_supervisor_field(exc, "elapsed_s")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


#: Number of child log lines captured for a restart incident record.
_LOG_TAIL_LINES: int = 10
_LOG_TAIL_MAX_BYTES: int = 16 * 1024


class RestartAwareMcpBridge:
    """SessionBridgeLike wrapper that auto-restarts the MCP server on crash.

    Bounded restart budget prevents unbounded retry loops. All process
    spawning continues to flow through ProcessManager via the injected
    LifecycleDeps so ProcessManager remains the single process authority.

    The endpoint URI is stable for the full bridge lifetime: the same host/port
    is reused on every restart so agents never see a changed MCP_ENDPOINT_ENV.
    Thread-safe: a lock guards all inner-process mutations so the background
    McpSupervisor can safely call check_health_and_restart_if_needed() while
    the main thread is executing agent output streaming.
    """

    def __init__(
        self,
        inner: StandaloneMcpProcess,
        *,
        restart_fn: Callable[[], StandaloneMcpProcess],
        restart_policy: McpRestartPolicy,
        run_id: str,
        probe_fn: Callable[[str, timedelta], None] | None = None,
        probe_timeout_fn: Callable[[], timedelta] | None = None,
        activity_relay: ActivityRelay | None = None,
        sleep_fn: Callable[[float], object] | None = None,
        workspace_root: Path | None = None,
        contention_signal_producer: Callable[[], bool] | None = None,
        preflight_timeout_fn: Callable[[], timedelta] | None = None,
    ) -> None:
        self._inner = inner
        self._restart_fn = restart_fn
        self._restart_policy = restart_policy
        # The session's run_id is the single identity the receipt and the
        # gate share. Storing it on the bridge lets ``bridge_env_for``
        # derive ``MCP_RUN_ID_ENV`` from the same value the submission
        # handler stamps receipts with — drift is structurally impossible
        # because there is no separate label parameter to disagree.
        self._run_id = run_id
        self._probe_fn = probe_fn
        self._probe_timeout_fn = probe_timeout_fn
        self._preflight_timeout_fn = preflight_timeout_fn
        self._activity_relay = activity_relay
        # S4: tests inject a ``FakeClock``/sentinel sleep so the
        # production 2.0-second default does not stall the suite.
        # Production uses ``time.sleep`` (default below).
        self._sleep_fn: Callable[[float], object] | None = sleep_fn
        self._restart_count = 0
        # Consecutive failed probes. A probe that times out proves the
        # server is slow, not dead: under load a healthy server routinely
        # misses one bounded probe window. Restarting on the first miss
        # killed working servers mid-call, so a restart needs
        # _PROBE_FAILURES_BEFORE_RESTART misses in a row. A process that
        # has actually exited still restarts immediately — that signal is
        # unambiguous.
        self._consecutive_probe_failures = 0
        # Tool-registry resets are tracked separately from crash
        # restarts so the orchestrator can distinguish a "tool-registry
        # rebuild" event from a "MCP server crashed" event. The cap
        # (_TOOL_REGISTRY_MAX_RESETS) is independent of the MCP restart
        # policy's max_restarts. See _tool_registry_resets property.
        self._tool_registry_resets = 0
        # Bridge identity counter: increments on every reset_tool_registry
        # and on every successful _perform_restart. The producer result
        # in check_health_and_restart_if_needed is anchored to
        # (self._inner.process.pid, self._bridge_generation) so a stale
        # probe can be discarded if the bridge changed while the
        # producer ran outside the lock.
        self._bridge_generation = 0
        self._lock = threading.Lock()
        # Workspace root used to locate the child MCP server log for the
        # durable restart-decision evidence.  Stored on the bridge so the
        # log tail can be captured at the r3-sink call site (which is
        # deep inside check_health_and_restart_if_needed, not on the
        # start_mcp_server call path).  None is safe: log_tail will be
        # None when the workspace root is unavailable.
        self._workspace_root: Path | None = workspace_root
        # R1: the parent-owned listening socket; set by
        # start_mcp_server after the spawn succeeds, closed in
        # shutdown. The field is declared on the instance (not the
        # class) so existing RestartAwareMcpBridge(...) call sites
        # stay backward-compatible.
        self._listening_socket: _socket_type | None = None
        # R3-durability: the path to this bridge's unique health-event
        # sink file. Set by start_mcp_server; never cleared on shutdown
        # so evidence is retained after bridge shutdown for incident triage.
        self._durable_sink_path: Path | None = None
        # R3 host-contention guard: consecutive probe-failures deferred
        # because a fresh workspace-monitor ``watch_capacity_predicted``
        # signal (or a producer-injected signal) suggests the host is
        # saturated, not the server. Reset on every successful probe.
        # Independent of ``_consecutive_probe_failures`` so a regular
        # three-miss policy still applies when no contention signal is
        # present.
        self._consecutive_probe_failures_deferred: int = 0
        # Test seam: a callable that returns True when the host is
        # currently under contention. Production calls
        # ``current_host_is_contended`` (the default) so the bounded
        # /proc reader is exercised. ``None`` means "use the default
        # production probe".
        self._contention_signal_producer: Callable[[], bool] | None = contention_signal_producer
        # Sliding window (seconds) for ``watch_capacity_predicted``
        # signals. Read from the policy when present; default 10.0 s
        # when the policy is a mock or predates the field.
        window_attr: object = getattr(restart_policy, "host_contention_window_seconds", 10.0)
        self._host_contention_window_seconds: float = (
            float(window_attr)
            if isinstance(window_attr, (int, float)) and not isinstance(window_attr, bool)
            else 10.0
        )

    @property
    def run_id(self) -> str:
        """The session's run identity — the receipt key the gate reads.

        See :class:`ralph.mcp.protocol._session_bridge_like.SessionBridgeLike`
        for the full contract; the property is the bridge-side realization
        of the protocol declaration.
        """
        return self._run_id

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def tool_registry_resets(self) -> int:
        """Number of times `reset_tool_registry()` has been called.

        Independent of `restart_count` (which tracks crash restarts) and
        the recovery controller's `max_recovery_attempts` (which tracks
        agent-invocation retries). The orchestrator can inspect this
        counter to diagnose which cap fired on a wedged run.
        """
        return self._tool_registry_resets

    @property
    def process(self) -> ProcessLike:
        """The currently running MCP server process.

        Satisfies the ``_BridgeWithProcess`` protocol so consumers (e.g. the
        parallel worker session factory) can read the server pid. Reads the
        inner process under the lock because a concurrent health-check
        restart may swap it.
        """
        with self._lock:
            return self._inner.process

    def start(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return self._inner.endpoint

    def endpoint_uri(self) -> str:
        return self._inner.endpoint

    def shutdown(self) -> None:
        if self._activity_relay is not None:
            self._activity_relay.close()
        self._inner.shutdown()
        # R1: close the parent-owned listening socket last so the
        # FD stays live for every other concurrent call site. The
        # bridge holds the listening socket for its full lifetime;
        # shutdown is the only path that releases it.
        listening_socket: _socket_type | None = getattr(self, "_listening_socket", None)
        if listening_socket is not None:
            # Best-effort: a foreign close on a shared FD is not
            # actionable from the bridge; the kernel has already
            # released the port.
            with contextlib.suppress(OSError):
                listening_socket.close()
            self._listening_socket = None
        # R3-durability: the durable health-event sink is per-bridge and
        # never deleted on shutdown. Each bridge owns a unique file so
        # evidence is retained after shutdown for incident triage.

    def _cooldown_after_failure(self) -> None:
        """Sleep for the policy cooldown, driven by an injectable clock.

        S4: the lifecycle spaces consecutive ``McpServerError``-class
        restarts by ``McpRestartPolicy.cooldown_seconds``. The
        function takes no argument: the policy is read off the
        bridge and the injected clock/sleep seam is used. Tests pass
        a ``FakeClock`` / sentinel ``sleep_fn`` so the production
        2.0-second default does not stall the suite. Called before
        EVERY restart attempt (healthy or failed): fresh child
        processes spike the same host load regardless of why the
        previous one died.
        """
        seconds = self._restart_policy.cooldown_seconds
        # Defensive: tests may inject a MagicMock as the policy
        # (the existing bridge tests pre-date the cooldown
        # field). Skip the sleep when the value is not a real
        # positive number so the failure path is exercised
        # without the bridge tripping on a non-numeric mock.
        if not isinstance(seconds, (int, float)) or not seconds > 0:
            return
        sleep_fn = cast("Callable[[float], object] | None", getattr(self, "_sleep_fn", None))
        if sleep_fn is None:
            sleep_fn = time.sleep
        sleep_fn(seconds)

    @property
    def activity_relay(self) -> ActivityRelay | None:
        """Conflict-only relay owned by this bridge, when one was configured."""
        return self._activity_relay

    def relay_health_error(self) -> str | None:
        """Return a latched relay fault before an invocation can be classified as idle."""
        return None if self._activity_relay is None else self._activity_relay.health_error()

    def register_activity_sink(self, sink: Callable[[str], None]) -> Callable[[], None]:
        """Register the parent watchdog for authenticated standalone MCP events."""
        if self._activity_relay is None:
            return lambda: None
        return self._activity_relay.register_sink(sink)

    def _read_mcp_log_tail(self) -> tuple[str, ...] | None:
        """Read the last 10 lines of the child MCP server log, if accessible.

        Returns ``None`` when the workspace root is not set, the log file
        does not exist, or the read fails.  ``None`` (not an empty tuple)
        signals "log was unavailable" so the durable record distinguishes
        "unavailable" from "log was present but empty".

        Reads only the bounded tail of the file, so a subprocess that
        logged megabytes before dying cannot flood this path.
        """
        root = self._workspace_root
        if root is None:
            return None
        log_path = mcp_server_log_path(root)
        try:
            size = mcp_server_log_size(root)
            if size == 0:
                # Empty file is readable-but-empty: []. Only a MISSING file
                # (stat fails) yields the unavailable sentinel None.
                if log_path.exists():
                    return ()
                return None
            with log_path.open("rb") as fh:
                # Bounded tail: seek near the end and read at most the
                # last chunk; a partial first line is dropped below.
                fh.seek(max(0, size - _LOG_TAIL_MAX_BYTES))
                chunk = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        lines = chunk.split("\n")
        if size > _LOG_TAIL_MAX_BYTES and len(lines) > 1:
            # Drop the possibly-truncated leading line.
            lines = lines[1:]
        if lines and lines[-1] == "":
            lines.pop()  # trailing newline, not a line of content
        return tuple(lines[-_LOG_TAIL_LINES:])

    def _run_health_probe(self) -> tuple[bool, float | None, bool, bool]:
        """Run the configured probe under lock.

        Returns (probe_failed, probe_duration_s, infra_error, overrun).
        """
        if self._probe_fn is None:
            return False, None, False, False
        base_timeout: timedelta = (
            self._probe_timeout_fn() if self._probe_timeout_fn is not None else timedelta(seconds=5)
        )
        preflight_timeout: timedelta = (
            self._preflight_timeout_fn()
            if self._preflight_timeout_fn is not None
            else mcp_preflight_timeout_from_env()
        )
        ceiling: timedelta = max(base_timeout, preflight_timeout - timedelta(seconds=1.0))
        # Cap the exponent so the timedelta multiply cannot overflow at very
        # high deferral counts (sustained host contention: a live child kept
        # deferring past 2**31 would otherwise raise ``OverflowError`` and
        # tear down the health-check path). The cap is far past the
        # realistic ceiling anyway: probe timeout is min'd against the
        # ``ceiling`` below, so anything above ~2**10 already saturates.
        deferred_count = max(0, self._consecutive_probe_failures_deferred)
        safe_exponent = min(deferred_count, _PROBE_TIMEOUT_EXPAWN_CAP)
        multiplier: int = 2**safe_exponent
        probe_timeout: timedelta = min(base_timeout * multiplier, ceiling)
        probe_start = time.monotonic()
        try:
            self._probe_fn(self._inner.endpoint, probe_timeout)
            return False, None, False, False
        except Exception as exc:
            elapsed = time.monotonic() - probe_start
            overrun = elapsed > probe_timeout.total_seconds() + _PROBE_DEADLINE_SLACK_S
            if (
                isinstance(exc, PreflightError)
                and self._activity_relay is not None
                and "SUPERVISION_INFRASTRUCTURE_FAILURE" in str(exc)
            ):
                self._activity_relay.record_sender_error(str(exc))
                return False, None, True, False
            return True, elapsed, False, overrun

    def _record_contention_deferral(
        self,
        *,
        probe_duration_s: float | None,
        deferral_count: int,
        stage: str = "probe_deferred_contention",
    ) -> None:
        """Write a durable sink record when a probe failure is deferred under contention."""
        target = (
            self._durable_sink_path
            if self._durable_sink_path is not None
            else get_durable_sink_path()
        )
        if target is None:
            return
        try:
            with _DURABLE_SINK_LOCK:
                target.parent.mkdir(parents=True, exist_ok=True)
                with (
                    target.open(  # filesystem-write-ok: append-only durable JSONL stream; each contention deferral must append
                        "a", encoding="utf-8"
                    ) as fh
                ):
                    record: dict[str, object] = {
                        "reason": "probe_deferred_contention",
                        "exit_status": None,
                        "probe_duration_s": probe_duration_s,
                        "deferral_count": deferral_count,
                        "log_tail": None,
                        "stage": stage,
                    }
                    fh.write(json.dumps(record))
                    fh.write("\n")
        except OSError:
            pass

    def _perform_restart(
        self,
        *,
        process_exited: bool,
        probe_duration_s: float | None,
    ) -> bool:
        """Perform MCP server restart under lock and record the decision.

        Must be called with self._lock held.
        """
        if self._restart_count >= self._restart_policy.max_restarts:
            reason = "probe failed" if not process_exited else "exited"
            raise McpServerError(
                f"MCP server {reason} and restart budget exhausted "
                f"(current={self._restart_count}, "
                f"max={self._restart_policy.max_restarts})",
                restart_count=self._restart_count,
            )

        self._restart_count += 1
        attempt_index = self._restart_count
        if not process_exited:
            logger.warning(
                "MCP server unresponsive (probe timed out or failed); restarting ({}/{})",
                attempt_index,
                self._restart_policy.max_restarts,
            )
        else:
            logger.warning(
                "MCP server process exited unexpectedly; restarting ({}/{})",
                attempt_index,
                self._restart_policy.max_restarts,
            )
        log_tail = self._read_mcp_log_tail()
        _record_restart_decision(
            sink_path=self._durable_sink_path,
            reason=(RestartReason.PROCESS_EXITED if process_exited else RestartReason.PROBE_FAILED),
            exit_status=self._inner.process.poll() if process_exited else None,
            probe_duration_s=probe_duration_s if not process_exited else None,
            log_tail=log_tail,
            stage="process_exited" if process_exited else "probe_failed",
        )
        self._cooldown_after_failure()
        try:
            self._inner.shutdown()
            self._inner = self._restart_fn()
        except Exception as restart_exc:
            raise McpServerError(
                f"MCP restart attempt {attempt_index}/"
                f"{self._restart_policy.max_restarts} failed: {restart_exc}",
                restart_count=self._restart_count,
                last_stage=_restart_error_fields(restart_exc)[0],
                elapsed_s=_restart_error_fields(restart_exc)[1],
            ) from restart_exc
        # Bump generation on every successful restart so a stale probe
        # captured against the prior inner is discarded by
        # _handle_failed_probe_under_lock.
        self._bridge_generation += 1
        self._consecutive_probe_failures = 0
        self._consecutive_probe_failures_deferred = 0
        logger.info(
            "MCP server restarted on stable endpoint {}; restart_count={}",
            self._inner.endpoint,
            self._restart_count,
        )
        return True

    def _handle_failed_probe_under_lock(
        self,
        *,
        fresh_b_signal: bool,
        probe_duration_s: float | None,
        probed_pid: int,
        probed_generation: int,
    ) -> bool:
        """Handle contention and failure accounting after a failed probe under lock.

        ``probed_pid`` and ``probed_generation`` are the bridge identity
        captured under the lock BEFORE the contention producer ran. If
        either changed while the producer was off the lock, the B-signal
        we hold belongs to a different bridge state and is discarded
        without touching any counter.
        """
        with self._lock:
            if (
                self._inner.process.pid != probed_pid
                or self._bridge_generation != probed_generation
            ):
                logger.debug("stale probe result discarded: bridge changed while producer ran")
                return False

            if self._inner.process.poll() is not None:
                return self._perform_restart(process_exited=True, probe_duration_s=None)

            # Sustained contention ceiling: if the deferral counter has reached
            # the cap, force a restart even when a fresh contention signal is
            # present, preventing indefinite deferral of a genuinely wedged child.
            if self._consecutive_probe_failures_deferred >= _DEFERRAL_COUNT_CEILING:
                logger.warning("deferral ceiling tripped; forcing replacement")
                return self._perform_restart(
                    process_exited=False, probe_duration_s=probe_duration_s
                )

            fresh_w_signal = bool(
                pop_recent_host_contention_signals(self._host_contention_window_seconds)
            )
            fresh_contention = fresh_w_signal or fresh_b_signal
            if fresh_contention:
                self._consecutive_probe_failures_deferred += 1
                self._record_contention_deferral(
                    probe_duration_s=probe_duration_s,
                    deferral_count=self._consecutive_probe_failures_deferred,
                    stage="probe_deferred_contention",
                )
                logger.debug(
                    "MCP server probe failed with fresh contention signal; deferring restart "
                    "(deferred={}, w={}, b={})",
                    self._consecutive_probe_failures_deferred,
                    fresh_w_signal,
                    fresh_b_signal,
                )
                return False

            self._consecutive_probe_failures_deferred = 0
            self._consecutive_probe_failures += 1
            if self._consecutive_probe_failures < _PROBE_FAILURES_BEFORE_RESTART:
                logger.debug(
                    "MCP server probe failed ({}/{} before restart); server still running",
                    self._consecutive_probe_failures,
                    _PROBE_FAILURES_BEFORE_RESTART,
                )
                return False

            return self._perform_restart(process_exited=False, probe_duration_s=probe_duration_s)

    def check_health_and_restart_if_needed(self) -> bool:
        """Check if MCP server is alive and responsive; restart if not.

        Treats the bridge as unhealthy when either (a) the subprocess has exited
        or (b) the subprocess is alive but the responsiveness probe times out or
        fails. On an unhealthy result the stale process is terminated, a new one
        is spawned via restart_fn (which reruns full preflight), and the bounded
        restart counter is incremented.

        Returns True when a restart was performed.
        Raises McpServerError when the restart budget is exhausted.
        Thread-safe: may be called from a background McpSupervisor thread.
        """
        with self._lock:
            if self._inner.process.poll() is not None:
                return self._perform_restart(process_exited=True, probe_duration_s=None)

            probe_failed, probe_duration_s, infra_error, overrun = self._run_health_probe()
            if infra_error:
                return False

            if not probe_failed:
                self._consecutive_probe_failures = 0
                self._consecutive_probe_failures_deferred = 0
                return False

            # Capture the probed bridge identity BEFORE releasing the
            # lock. The producer below runs OFF the lock; while it
            # runs, the main thread may call reset_tool_registry or a
            # concurrent probe may succeed and reset counters. The
            # helper revalidates this identity under the lock and
            # discards the result if the bridge changed.
            probed_pid = self._inner.process.pid
            probed_generation = self._bridge_generation

        # Probe failed: lock is RELEASED before calling producer()
        # A probe overrun is classified as host contention (C3).
        fresh_b_signal_from_overrun = False
        if probe_failed and overrun:
            record_host_contention_signal("probe_overrun")
            fresh_b_signal_from_overrun = True
        producer = self._contention_signal_producer or current_host_is_contended
        try:
            fresh_b_signal = bool(producer())
        except Exception:
            fresh_b_signal = False
        fresh_b_signal = fresh_b_signal or fresh_b_signal_from_overrun

        return self._handle_failed_probe_under_lock(
            fresh_b_signal=fresh_b_signal,
            probe_duration_s=probe_duration_s,
            probed_pid=probed_pid,
            probed_generation=probed_generation,
        )

    def reset_tool_registry(self) -> None:
        """Rebuild the visible tool list by rerunning the restart path.

        Called by the recovery controller when the failure classifier
        flags a tool-availability failure (the post-tool-result wedge:
        live server reports ``No such tool available: mcp__<server>__<tool>``
        because the agent's ``tools/list`` snapshot lost the alias after
        a prior restart, retry, or transient recovery).

        This is a separate counter from ``_restart_count`` so the
        orchestrator can distinguish a tool-registry rebuild from a
        crash restart, while also charging against the shared
        ``_restart_count`` and obeying ``_restart_policy.max_restarts``.
        The reset cap is ``_TOOL_REGISTRY_MAX_RESETS``.

        Raises:
            McpServerError: when the reset cap or shared restart budget
                is exhausted. The message contains the substring
                ``'tool-registry-reset exhausted'`` or
                ``'restart budget exhausted'``.
        """
        with self._lock:
            # Bump the bridge generation FIRST so any in-flight stale
            # probe result (captured against the previous inner) is
            # discarded by _handle_failed_probe_under_lock even if the
            # reset later fails.
            self._bridge_generation += 1
            if self._tool_registry_resets >= _TOOL_REGISTRY_MAX_RESETS:
                raise McpServerError(
                    "MCP tool-registry-reset exhausted "
                    f"({_TOOL_REGISTRY_MAX_RESETS}) "
                    f"after {self._tool_registry_resets} reset(s)",
                    restart_count=self._restart_count,
                )
            if self._restart_count >= self._restart_policy.max_restarts:
                raise McpServerError(
                    "MCP server restart budget exhausted "
                    f"(current={self._restart_count}, "
                    f"max={self._restart_policy.max_restarts})",
                    restart_count=self._restart_count,
                )
            # S1: increment FIRST so a raise from _restart_fn is
            # reflected in the counts.
            self._tool_registry_resets += 1
            self._restart_count += 1
            attempt_index = self._tool_registry_resets
            logger.info(
                "Resetting MCP tool registry ({}/{}); endpoint={}",
                attempt_index,
                _TOOL_REGISTRY_MAX_RESETS,
                self._inner.endpoint,
            )
            # Reuse the same restart path so preflight runs again and
            # the tools/list snapshot is rebuilt. We do NOT call
            # self._restart_fn() directly here because that would skip
            # the preflight (the restart_fn is a private seam used by
            # check_health_and_restart_if_needed). Instead, the caller
            # is expected to invoke the bridge's preflight + spawn path
            # via a small wrapper. For now, the simplest correct
            # behavior is: rotate the inner process via the same
            # _restart_fn so a fresh registry is built. The same
            # endpoint is reused (the port is reserved at bridge
            # construction and stays stable across resets).
            self._cooldown_after_failure()
            try:
                self._inner.shutdown()
                self._inner = self._restart_fn()
            except Exception as restart_exc:
                # Counter already advanced (S1); raise a typed error
                # so the recovery loop's ``except McpServerError:
                # raise`` propagates the type.
                raise McpServerError(
                    f"MCP tool-registry-reset attempt {attempt_index}/"
                    f"{_TOOL_REGISTRY_MAX_RESETS} failed: {restart_exc}",
                    restart_count=self._restart_count,
                    last_stage=_restart_error_fields(restart_exc)[0],
                    elapsed_s=_restart_error_fields(restart_exc)[1],
                ) from restart_exc
            # NEW BEHAVIOR: actively verify the mcp__<server>__<tool>
            # alias is present in the post-respawn tools/list
            # response. Pre-fix, the respawn path assumed the alias
            # would be rebuilt by the same preflight that constructs
            # the new inner process; if the alias is missing, the
            # wedge reappears silently. The new method raises
            # McpServerError with a NEW distinct substring
            # 'mcp alias missing after respawn' so the operator can
            # diagnose the regression.
            self._verify_alias_present()

    def _verify_alias_present(self) -> None:
        """Verify the canonical mcp alias is present after a respawn.

        Sends a JSON-RPC ``tools/list`` request to the inner process's
        HTTP endpoint via ``urllib.request`` and parses the response.
        If the alias ``mcp__<server>__<tool>`` is absent in the
        response, raises :class:`McpServerError` with the distinct
        substring ``'mcp alias missing after respawn'`` so the
        orchestrator can branch on it independently of the existing
        ``'tool-registry-reset exhausted'`` cap error.

        When the inner process is a MagicMock (e.g. in unit tests
        that do not exercise the HTTP path), the method logs a
        debug line and returns without raising. The error path
        is pinned by
        ``tests/test_live_tool_unavailable_recovery.py::test_bridge_reset_tool_registry_fails_closed_when_alias_probe_fails``,
        which drives ``reset_tool_registry()`` with a
        monkeypatched ``_http_tools_list_names`` raising
        ``TimeoutError`` and asserts the resulting
        ``McpServerError`` matches the ``'alias verify probe
        failed after respawn'`` substring.

        The HTTP round-trip is bounded by
        ``_RESPAWN_ALIAS_VERIFY_TIMEOUT_S`` so a wedged inner
        process does not stall the recovery path.
        """
        endpoint = self._inner.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            logger.debug("alias-verify skipped: inner process endpoint is not a string")
            return
        # MagicMock detection: a MagicMock inner process returns a
        # MagicMock for any attribute access. If ``endpoint`` is
        # not a real string or the inner process does not have a
        # ``process`` attribute, skip the HTTP call and return
        # silently so unit tests using MagicMock do not break.
        inner_process: object = getattr(self._inner, "process", None)
        if inner_process is None or not hasattr(inner_process, "poll"):
            logger.debug("alias-verify skipped: inner process lacks poll() (MagicMock?)")
            return
        try:
            tool_names = _http_tools_list_names(endpoint, timeout=_RESPAWN_ALIAS_VERIFY_TIMEOUT_S)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            msg = f"mcp alias verify probe failed after respawn: endpoint={endpoint!r} error={exc}"
            logger.error(msg)
            raise McpServerError(
                msg,
                restart_count=self._restart_count,
            ) from exc
        expected_alias = f"mcp__{_ALIAS_VERIFY_SERVER_NAME}__{_ALIAS_VERIFY_TOOL_NAME}"
        if expected_alias in tool_names:
            return
        msg = (
            f"mcp alias missing after respawn: alias={expected_alias!r} "
            f"endpoint={endpoint} tool_count={len(tool_names)} "
            f"sample_tools={sorted(tool_names)[:5]!r}"
        )
        logger.error(msg)
        raise McpServerError(
            msg,
            restart_count=self._restart_count,
        )

    def reset_session_budget(self) -> None:
        """Clear the inner subprocess's pending completion admissions.

        The McpServer is a per-subprocess singleton; the bridge is its only
        client. Each attempt boundary (e.g. the start of every
        ``effect_executor._run_attempt``) clears pending identity-scoped
        completion admissions. Phase-wide elapsed time is unaffected by attempt
        resets. The reset is exposed as the custom JSON-RPC method
        ``notifications/reset_wrapup`` over HTTP; the McpServer handles it
        via ``McpServer._dispatch_request`` by calling
        ``McpServer.reset_session_budget()``.

        Best-effort: transport errors are logged and swallowed (the
        recovery controller's attempt loop will retry on the next attempt
        boundary). The HTTP POST is bounded by ``_RESET_WRAPUP_TIMEOUT_S``
        so ``audit_mcp_timeout`` stays green; a wedged or just-respawned
        inner process does not stall the attempt.

        When the inner process is a MagicMock (e.g. in unit tests that do
        not exercise the HTTP path) the reset is a no-op. Same pattern as
        :meth:`_verify_alias_present` so the bridge tests stay hermetic
        (a real HTTP POST against a fake endpoint would otherwise hit the
        1.0-second per-test timeout and time out the suite).

        Thread-safe: holds ``self._lock`` to serialize with concurrent
        ``check_health_and_restart_if_needed`` and ``reset_tool_registry``
        calls so a respawn and a reset cannot interleave.

        Note: the intra-bridge respawn paths
        (:meth:`check_health_and_restart_if_needed` and
        :meth:`reset_tool_registry`) do NOT call this method. A fresh
        subprocess starts with ``elapsed=0`` by construction, so the next
        attempt boundary's reset is sufficient — adding a second reset
        inside the bridge would only matter on a tight crash-respawn cycle
        and would risk blocking on a 1.0s test-timeout-budget in unit
        tests that use a ``FakeProcess`` (which has ``poll()`` but no
        real HTTP endpoint). The attempt-boundary wire-up at the top of
        :func:`effect_executor._run_attempt` is the single production
        seam that drives the reset.
        """
        endpoint = self._inner.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            logger.debug("reset_session_budget skipped: inner process endpoint is not a string")
            return
        # MagicMock / fake-bridge detection: a MagicMock inner process
        # (e.g. ``MagicMock(spec=StandaloneMcpProcess)``) does NOT have
        # ``.process.poll()`` because the spec excludes instance
        # attributes. A real ``_NoopProcess`` test fake DOES have poll.
        # The check below matches the same pattern as
        # :meth:`_verify_alias_present` so the respawn paths stay
        # hermetic in unit tests.
        inner_process: object = getattr(self._inner, "process", None)
        if inner_process is None or not hasattr(inner_process, "poll"):
            logger.debug("reset_session_budget skipped: inner process lacks poll() (MagicMock?)")
            return
        request_payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": "notifications/reset_wrapup",
            "params": {},
            "id": "reset-wrapup-1",
        }
        body = json.dumps(request_payload).encode("utf-8")
        with self._lock:
            request = urllib.request.Request(
                endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                response = cast(
                    "IO[bytes]",
                    urllib.request.urlopen(request, timeout=_RESET_WRAPUP_TIMEOUT_S),
                )
                try:
                    # Bound the read so a misbehaving upstream cannot OOM the
                    # parent by streaming an unbounded response body (AC-08).
                    # The body is opaque here and discarded, so truncation is
                    # harmless.
                    response_data = response.read(_LIFECYCLE_MAX_RESPONSE_BYTES)
                finally:
                    response.close()
                _ = response_data  # payload is opaque; the wire-level seam
                # is the method dispatch, not the response body.
            except (
                urllib.error.URLError,
                OSError,
                TimeoutError,
                ValueError,
            ) as exc:
                logger.warning(
                    "reset_session_budget POST to {} failed (swallowed, best-effort): {}",
                    endpoint,
                    exc,
                )
                return


def check_mcp_bridge_health(bridge: SessionBridgeLike) -> None:
    """Perform a health check on the MCP bridge, restarting if it crashed.

    Only has an effect when ``bridge`` is a :class:`RestartAwareMcpBridge`.
    Raises :class:`McpServerError` when the restart budget is exhausted.
    """
    if isinstance(bridge, RestartAwareMcpBridge):
        bridge.check_health_and_restart_if_needed()


def start_mcp_server(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    upstream_registry: UpstreamRegistry | None = None,
    deps: LifecycleDeps | None = None,
    extras: McpServerExtras | None = None,
) -> RestartAwareMcpBridge:
    """Start a standalone Ralph MCP HTTP subprocess and verify tool reachability.

    Returns a :class:`RestartAwareMcpBridge` that can auto-restart the server
    on crash up to the ``extras.restart_policy`` budget (default: 20 restarts,
    defined by :class:`McpRestartPolicy`).

    R1: the bridge holds ONE listening socket for its full lifetime
    (created by ``deps.reserve_listening_socket`` or the default
    ``_reserve_listening_socket``). Every spawned child inherits the
    FD by number, so a client connecting during a respawn queues in
    the kernel backlog instead of being refused with
    ``ECONNREFUSED``. Production paths always use the listening-socket hook.

    R2: the bridge hands the cached tool catalog to every respawn
    via ``UPSTREAM_MCP_TOOL_CATALOG_ENV`` so the respawn path does
    no network discovery. The first start still probes upstreams
    once and the parent reuses the catalog thereafter.
    """
    effective_extras = extras or McpServerExtras()
    lifecycle_deps = deps or _default_lifecycle_deps()
    root = _workspace_root(workspace)
    visible_tools = _visible_mcp_tool_names_owned(
        session, workspace, upstream_registry=upstream_registry
    )

    # I1b: each spawn resolves the catalog snapshot fresh from the
    # parent-owned cache, so a restart never re-uses a stale snapshot.
    def _resolve_catalog_snapshot() -> UpstreamToolCatalogSnapshot:
        try:
            return get_tool_catalog_snapshot(root)
        except ImportError:
            # Import error is a structural defect that should never
            # reach production, but keep the spawn path alive by
            # treating the catalog as absent.
            return UpstreamToolCatalogSnapshot(present=False)

    # R1: bind the listening socket ONCE in the parent and reuse it
    # across every respawn. The port comes from a live LISTEN socket
    # the parent owns for the bridge's full lifetime; a bind-then-
    # release port number is never used, so a client connecting
    # during a respawn queues in the backlog instead of receiving
    # ECONNREFUSED.
    reserve = (
        lifecycle_deps.reserve_listening_socket
        if lifecycle_deps.reserve_listening_socket is not None
        else _reserve_listening_socket
    )
    _host, port, listening_socket = reserve()
    # Listening-socket cleanup invariant: if anything fails between
    # the reservation and handing the socket to the bridge, the
    # parent closes it here; otherwise the FD (and the bound port)
    # leak for the lifetime of the process. Once the bridge owns the
    # socket (``bridge._listening_socket``), its ``shutdown`` closes
    # it deterministically.
    try:
        inner = _spawn_mcp_process(
            root,
            session,
            lifecycle_deps,
            effective_extras.phase,
            effective_extras.extra_env,
            visible_tools,
            port=port,
            inherited_fd=listening_socket.fileno(),
            respawn=False,
            tool_catalog_snapshot=_resolve_catalog_snapshot(),
            activity_relay=effective_extras.activity_relay,
        )

        def _restart_fn() -> StandaloneMcpProcess:
            return _spawn_mcp_process(
                root,
                session,
                lifecycle_deps,
                effective_extras.phase,
                effective_extras.extra_env,
                visible_tools,
                port=port,
                inherited_fd=listening_socket.fileno(),
                respawn=True,
                tool_catalog_snapshot=_resolve_catalog_snapshot(),
                activity_relay=effective_extras.activity_relay,
            )

        bridge = RestartAwareMcpBridge(
            inner,
            restart_fn=_restart_fn,
            restart_policy=effective_extras.restart_policy or McpRestartPolicy(),
            run_id=session.run_id,
            probe_fn=lifecycle_deps.probe,
            probe_timeout_fn=lifecycle_deps.probe_timeout,
            preflight_timeout_fn=lifecycle_deps.preflight_timeout,
            activity_relay=effective_extras.activity_relay,
            workspace_root=root,
        )
        # Stash the listening socket on the bridge so ``shutdown`` can
        # close it deterministically (R1 invariant: the port stays ours
        # until bridge shutdown, never released early).
        bridge._listening_socket = listening_socket
        # R3-durability: each bridge owns a unique health-event sink file.
        # The path is persisted on the bridge object so shutdown does not
        # delete it (evidence is retained across shutdown for incident triage).
        bridge._durable_sink_path = _configure_bridge_sink(root, uuid.uuid4().hex[:12])
    except BaseException:
        with contextlib.suppress(OSError):
            listening_socket.close()
        raise
    return bridge


def _startup_diagnostic_suffix(root: Path, log_offset: int) -> str:
    """Render the child's own startup output as a suffix for the parent's error."""

    diagnostic = read_mcp_server_log_since(root, log_offset)
    if not diagnostic:
        return ""
    return f"\nMCP server output ({mcp_server_log_path(root)}):\n{diagnostic}"


def _await_mcp_startup_or_failure(
    bridge: StandaloneMcpProcess,
    *,
    deps: LifecycleDeps,
    endpoint: str,
    visible_tools: list[str],
    port: int,
    env: Mapping[str, str],
    spawn_start: float,
    root: Path,
    log_offset: int,
    stage_id: str,
) -> None:
    """Observe preflight and child process termination under an absolute startup deadline."""
    preflight_timeout_td = deps.preflight_timeout()
    preflight_timeout_s = preflight_timeout_td.total_seconds()
    try:
        whole_startup_s = mcp_whole_startup_budget_from_env(env)
    except Exception:
        whole_startup_s = preflight_timeout_s
    startup_deadline_s = min(preflight_timeout_s, whole_startup_s)
    if startup_deadline_s >= preflight_timeout_s:
        startup_deadline_s = max(0.1, preflight_timeout_s - 0.5)

    preflight_error: list[Exception] = []
    preflight_done = threading.Event()

    def _run_preflight() -> None:
        try:
            deps.preflight(endpoint, visible_tools, preflight_timeout_td)
        except Exception as exc:
            preflight_error.append(exc)
        finally:
            preflight_done.set()

    preflight_thread = threading.Thread(
        target=_run_preflight,
        name=f"mcp-preflight-{port}",
        daemon=True,
    )
    preflight_thread.start()

    early_exit_rc: int | None = None
    deadline_exceeded = False
    poll_interval = 0.02

    while True:
        if preflight_done.wait(timeout=poll_interval):
            break
        rc = bridge.process.poll()
        if rc is not None:
            early_exit_rc = rc
            break
        if (time.monotonic() - spawn_start) >= startup_deadline_s:
            deadline_exceeded = True
            break

    elapsed_s = time.monotonic() - spawn_start

    if early_exit_rc is None and not deadline_exceeded and not preflight_error:
        return

    returncode = bridge.process.poll()
    bridge.shutdown()
    diagnostic = _startup_diagnostic_suffix(root, log_offset)
    last_stage: str | None = _read_start_stage(root, spawn_id=stage_id)
    last_stage_str: str = last_stage if last_stage is not None else "last_stage_unknown"

    if early_exit_rc is not None or returncode is not None:
        effective_rc = early_exit_rc if early_exit_rc is not None else returncode
        cause = preflight_error[0] if preflight_error else None
        err = McpServerError(
            f"MCP server process exited before endpoint {endpoint} became ready "
            f"(rc={effective_rc}){diagnostic}",
            restart_count=0,
            last_stage=last_stage_str,
            elapsed_s=elapsed_s,
        )
        if cause is not None:
            raise err from cause
        raise err

    if deadline_exceeded:
        cause = preflight_error[0] if preflight_error else None
        err = McpServerError(
            f"MCP server startup exceeded deadline of {startup_deadline_s:.3f}s "
            f"before endpoint {endpoint} became ready{diagnostic}",
            restart_count=0,
            last_stage=last_stage_str,
            elapsed_s=elapsed_s,
        )
        if cause is not None:
            raise err from cause
        raise err

    raise McpServerError(
        f"MCP server endpoint {endpoint} did not become ready: {preflight_error[0]}{diagnostic}",
        restart_count=0,
        last_stage=last_stage_str,
        elapsed_s=elapsed_s,
    ) from preflight_error[0]


def _spawn_mcp_process(
    root: Path,
    session: SessionLike,
    deps: LifecycleDeps,
    phase: str | None,
    _extra_env: dict[str, str] | None,
    _visible_tools: list[str],
    *,
    port: int,
    inherited_fd: int | None = None,
    respawn: bool = False,
    tool_catalog_snapshot: UpstreamToolCatalogSnapshot | None = None,
    activity_relay: ActivityRelay | None = None,
) -> StandaloneMcpProcess:
    """Spawn a fresh MCP server process and run preflight validation.

    R1: when ``inherited_fd`` is set, the child is given the FD by
    number (``pass_fds=``) and the ``RALPH_MCP_INHERITED_FD`` env
    var; the child adopts the listening socket without re-binding.
    R2: when ``respawn`` is set, ``RALPH_MCP_RESPAWN=1`` is added so
    the runtime takes the catalog-only path (zero network I/O before
    serving). ``tool_catalog_snapshot`` is applied after the extra_env
    merge so the parent-owned catalog state unconditionally wins over
    worker-injected env values.
    """
    endpoint = f"http://127.0.0.1:{port}/mcp"
    # Recorded BEFORE the spawn so a startup failure reports only what THIS
    # child wrote; the log is append-only and shared across restarts.
    log_offset = mcp_server_log_size(root)
    session_file: Path | None = None
    bridge_run_id = session.run_id
    # Ensure session_id is available; raise early rather than falling back silently.
    session_id_value: str = session.session_id
    if not session_id_value:
        raise McpServerError(
            "session_id missing",
            restart_count=0,
            last_stage="snapshot_missing",
        )
    snap_dir = snapshot_dir(root, bridge_run_id)
    snap_session_file: Path = snap_dir / "session.json"
    # Compute broker_secret_fingerprint using the helper.
    broker_secret_val: str | None = getattr(session, "broker_secret", None)
    broker_fingerprint: str | None = _finger(broker_secret_val)
    # Write snapshot on first spawn (when session.json does not yet exist).
    if not snap_session_file.exists():
        write_snapshot(
            snap_dir,
            session_id=session_id_value,
            run_id=bridge_run_id,
            broker_secret_fingerprint=broker_fingerprint,
        )
    # Use the injection seam when provided (tests + production seam); otherwise
    # use the snapshot file so cross-respawn session_id persistence works.
    try:
        session_file = (
            deps.create_session_file(root, session)
            if deps.create_session_file is not None
            else snap_session_file
        )
        env = deps.subprocess_env(session_file)
        # Persist session_id in the subprocess environment so runtime_session.py
        # can read it on respawn without falling back to a new factory.
        env[MCP_SESSION_ID_ENV] = session_id_value
        if _extra_env:
            # Merge extra_env so the subprocess inherits worker-specific env vars
            # (e.g. WORKER_ARTIFACT_DIR for parallel workers).
            env.update(_extra_env)
        # I1b: the parent-owned catalog snapshot takes precedence over
        # anything merged from extra_env (present -> set even if empty,
        # absent -> pop).
        if tool_catalog_snapshot is not None:
            apply_tool_catalog_env(env, tool_catalog_snapshot)
        if activity_relay is not None:
            env.update(activity_relay.server_environment())
        if inherited_fd is not None:
            env[MCP_INHERITED_FD_ENV] = str(inherited_fd)
        if respawn:
            env[MCP_RESPAWN_ENV] = "1"
        # D-03: a fresh per-spawn stage identity is generated by the
        # parent and handed to the child so the child's stage tracker
        # writes to a file the parent can read on preflight failure.
        # Without this, both sides would compute a different import-time
        # UUID and ``read_last_stage`` would always return ``None`` from
        # the parent's perspective.
        stage_id = env.setdefault(MCP_STAGE_ID_ENV, str(uuid.uuid4()))
        pass_fds: tuple[int, ...] = (inherited_fd,) if inherited_fd is not None else ()
        spawn_start = time.monotonic()
        process = deps.spawn_process(
            [
                sys.executable,
                "-m",
                "ralph.mcp.server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workspace",
                str(root),
            ],
            root,
            env,
            phase=phase,
            pass_fds=pass_fds,
        )
        bridge = StandaloneMcpProcess(endpoint=endpoint, process=process, session_file=session_file)
    except BaseException:
        if session_file is not None:
            session_file.unlink(missing_ok=True)
        raise

    _await_mcp_startup_or_failure(
        bridge,
        deps=deps,
        endpoint=endpoint,
        visible_tools=_visible_tools,
        port=port,
        env=env,
        spawn_start=spawn_start,
        root=root,
        log_offset=log_offset,
        stage_id=stage_id,
    )

    return bridge


def shutdown_mcp_server(bridge: SessionBridgeLike) -> None:
    """Shutdown MCP server process."""
    bridge.shutdown()


def _visible_mcp_tool_names_owned(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    upstream_registry: UpstreamRegistry | None = None,
) -> list[str]:
    return visible_owned_tool_names(
        session,
        workspace,
        upstream_registry=upstream_registry,
        include_aliases=True,
    )


def _workspace_root(workspace: WorkspaceLike) -> Path:
    if isinstance(workspace, FsWorkspace):
        return workspace.root
    root_value = cast(
        "Path | str | None", getattr(workspace, "root", None)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    if isinstance(root_value, Path):
        return root_value.resolve()
    if isinstance(root_value, str):
        return Path(root_value).expanduser().resolve()
    msg = "Workspace root must be explicit when starting the MCP server"
    raise ValueError(msg)


def _subprocess_env(session_file: Path) -> dict[str, str]:
    env = dict(os.environ)
    env[SESSION_FILE_ENV] = str(session_file)
    env[MCP_OWNER_PID_ENV] = str(os.getpid())
    # R4 / R4-b: set in the parent so the child inherits unbuffered
    # streams from the start — setting it inside the already-running
    # child cannot reconfigure its own stdio.
    env["PYTHONUNBUFFERED"] = "1"
    package_root = str(_PACKAGE_ROOT)
    # The MCP server subprocess runs ``sys.executable -m ralph.mcp.server``
    # and MUST resolve the SAME package tree the parent imported
    # (``_PACKAGE_ROOT``). An ambient PYTHONPATH exported by an outer
    # launcher (e.g. a pipx-installed copy of this same package's
    # site-packages, observed live when an outer Ralph orchestrates a
    # development cycle inside a worktree) sorts AHEAD of the cwd on a
    # plain ``python -m`` invocation, so merely prepending the package
    # root is not sufficient for every entry: the outer launcher can
    # inject the same path first. Replace every inherited entry that
    # already provides the ``ralph`` package with the package root, and
    # drop duplicates, so the spawned server deterministically imports
    # the worktree's code (measured regression: the spawned server served
    # the stale pipx tree, which predates the Kimi schema-flavor
    # negotiation, and Moonshot rejected the composed root schemas with
    # HTTP 400 on every live smoke).
    seen: set[str] = set()
    entries: list[str] = []
    for raw in env.get("PYTHONPATH", "").split(os.pathsep):
        entry = raw.strip()
        if not entry:
            continue
        candidate = package_root if (Path(entry) / "ralph" / "__init__.py").is_file() else entry
        if candidate not in seen:
            seen.add(candidate)
            entries.append(candidate)
    if package_root not in seen:
        entries.insert(0, package_root)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


def _spawn_process(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    *,
    phase: str | None = None,
    spawn: Callable[[list[str], SpawnOptions], ManagedProcess] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> ProcessLike:
    label = f"phase:{phase}:mcp-server" if phase else "mcp-server"
    # Persist server output instead of discarding it: with DEVNULL, a crash
    # inside a request handler (e.g. an AttributeError after SSE headers) left
    # no trace anywhere while the client hung to its request timeout (-32001).
    log_path = mcp_server_log_path(cwd)
    # filesystem-write-ok: MCP server live diagnostic log directory
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(  # filesystem-write-ok: append-only live MCP diagnostic stream; resource-lifecycle-ok: fd closed in finally
        log_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o644,
    )
    try:
        spawn_fn = spawn if spawn is not None else get_process_manager().spawn
        return spawn_fn(
            command,
            SpawnOptions(
                cwd=str(cwd),
                env=env,
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
                label=label,
                pass_fds=pass_fds,
                allow_activity_relay_controls=True,
                allow_broker_secret=True,
            ),
        )
    finally:
        # The child holds its own duplicate of the fd from spawn time; the
        # parent's copy must be released or each restart leaks a descriptor.
        os.close(log_fd)


def _default_lifecycle_deps() -> LifecycleDeps:
    return LifecycleDeps(
        reserve_listening_socket=_reserve_listening_socket,
        create_session_file=_create_session_file,
        subprocess_env=_subprocess_env,
        spawn_process=_spawn_process,
        preflight=preflight_http_mcp_server_tools,
        preflight_timeout=mcp_preflight_timeout_from_env,
        probe=probe_mcp_http_endpoint,
        probe_timeout=mcp_probe_timeout_from_env,
    )


def _create_session_file(root: Path, session: SessionLike) -> Path:
    """Create one run-scoped MCP handshake file outside the watched workspace.

    The subprocess receives the absolute path through ``MCP_SESSION_FILE_ENV``;
    no user-visible or resume-facing contract requires this transient handshake
    payload to live below ``root``. Keeping it in the system temporary directory
    prevents a server start or restart from emitting workspace watch events.
    """
    del root
    fd, temp_path = tempfile.mkstemp(prefix="ralph-mcp-session-", suffix=".json")
    path = Path(temp_path)
    try:
        with os.fdopen(  # filesystem-write-ok: transient handshake stream
            fd, "w", encoding="utf-8"
        ) as stream:
            stream.write(session_payload_json(session))
        return path
    except BaseException:
        # filesystem-write-ok: cleanup of an unpublished transient handshake file after write failure
        path.unlink(missing_ok=True)
        raise


def identity_is_serialisable(identity: MultimodalModelIdentity) -> bool:
    """Decide whether provider or transport identity crosses the subprocess boundary."""
    return identity.is_known() or identity.transport is not None


def session_payload_json(session: SessionLike) -> str:
    """Serialize the session metadata to a compact JSON string for MCP handshake.

    IN-PROCESS ONLY: ``delegated_agent_id``,
    ``delegated_model_identity`` and ``delegated_capability_profile`` are
    deliberately NOT written here. Nothing in production sets them --
    they exist for a sub-agent delegation path that is not wired -- so
    serialising them would invent a wire format for a feature that has
    no producer.

    The consequence, stated so it is not discovered the hard way: a
    delegated identity set on a parent session does not survive this
    boundary, and the child resolves as the session itself. Anyone
    wiring delegation must add the three keys here AND to
    ``FileBackedSession``, or the guards in
    :func:`ralph.mcp.multimodal.capabilities.caller_identity_for` will
    silently judge the parent instead of the delegate on the far side.
    ``tests/test_delegated_identity_boundary.py`` pins this.
    """
    session_payload: dict[str, object] = {
        "session_id": session.session_id,
        "run_id": session.run_id,
        "drain": session.drain,
        "capabilities": sorted(session.capabilities),
    }
    raw_activity_only_supervision: object = getattr(session, "activity_only_supervision", False)
    activity_only_supervision = (
        raw_activity_only_supervision if isinstance(raw_activity_only_supervision, bool) else False
    )
    if activity_only_supervision:
        session_payload["activity_only_supervision"] = True
    raw_parallel_worker: object = getattr(session, "parallel_worker", False)
    if bool(raw_parallel_worker):
        session_payload["parallel_worker"] = True
    raw_worker_artifact_dir: object = getattr(session, "worker_artifact_dir", None)
    if isinstance(raw_worker_artifact_dir, Path):
        session_payload["worker_artifact_dir"] = str(raw_worker_artifact_dir)
    raw_worker_namespace: object = getattr(session, "worker_namespace", None)
    if isinstance(raw_worker_namespace, Path):
        session_payload["worker_namespace"] = str(raw_worker_namespace)
    raw_allowed_roots: object = getattr(session, "allowed_roots", ())
    if isinstance(raw_allowed_roots, tuple) and raw_allowed_roots:
        session_payload["allowed_roots"] = [str(path) for path in raw_allowed_roots]
    raw_identity: object = getattr(session, "model_identity", None)
    # An identity is worth serialising when EITHER half is resolved. The
    # gate used to be ``is_known()`` alone (provider resolved), which
    # dropped a transport-tagged unknown-provider identity on the way to
    # the subprocess -- and the multimodal layer keys some delivery
    # decisions on the transport, so those guards died at this boundary
    # even though the parent session had the information.
    if isinstance(raw_identity, MultimodalModelIdentity) and identity_is_serialisable(raw_identity):
        # Canonicalised on the way OUT, like every other seam that
        # writes a transport spelling. This one writes what it was
        # handed, so a hand-constructed parent identity of ``'CODEX'``
        # or ``'  codex  '`` was recorded verbatim and carried into the
        # verdict ``reason`` strings, giving one run several different
        # capability digests. Every production constructor normalises
        # upstream, so this changes no delivery decision -- it makes the
        # last seam agree with the claim the others already meet.
        session_payload["model_identity"] = {
            "provider": raw_identity.provider,
            "model_id": raw_identity.model_id,
            "transport": payload_transport(raw_identity.transport),
        }
    raw_profile: object = getattr(session, "capability_profile", None)
    if isinstance(raw_profile, ResolvedCapabilityProfile):
        # Re-based on the identity being serialised beside it. A profile
        # whose own identity predates a declared transport would
        # otherwise record verdicts the runtime does not act on, so the
        # child's payload and the wire ledger disagreed with what was
        # actually delivered.
        identity_for_profile = (
            raw_identity
            if isinstance(raw_identity, MultimodalModelIdentity)
            else raw_profile.identity
        )
        session_payload["capability_profile"] = profile_for_caller(
            raw_profile, identity_for_profile
        ).to_payload()
    elif isinstance(raw_identity, MultimodalModelIdentity) and identity_is_serialisable(
        raw_identity
    ):
        session_payload["capability_profile"] = resolve_capability_profile(
            raw_identity
        ).to_payload()
    # AC-11: when the parent session owns an exec resource resolver,
    # serialize the trusted spill roots so the subprocess session
    # (FileBackedSession) can re-construct a matching resolver. This is
    # the only path through which ``ralph://exec/<spill-name>`` URIs
    # returned by ``format=summary`` exec calls are re-readable inside
    # the production subprocess path.
    exec_resolver: object = getattr(session, "exec_resource_resolver", None)
    if exec_resolver is not None:
        resolver_roots: list[str] = []
        spill_roots_attr: object = getattr(exec_resolver, "spill_roots", ())
        if isinstance(spill_roots_attr, tuple):
            for root_path in spill_roots_attr:
                if isinstance(root_path, Path):
                    resolver_roots.append(str(root_path))
                    continue
                if isinstance(root_path, str) and root_path:
                    resolver_roots.append(root_path)
        if resolver_roots:
            session_payload["exec_spill_roots"] = resolver_roots
    return json.dumps(session_payload)


__all__ = [
    "LifecycleDeps",
    "McpRestartPolicy",
    "McpServerError",
    "McpServerExtras",
    "ProcessLike",
    "RestartAwareMcpBridge",
    "SessionBridgeLike",
    "SpawnProcess",
    "StandaloneMcpProcess",
    "check_mcp_bridge_health",
    "mcp_server_log_path",
    "session_payload_json",
    "shutdown_mcp_server",
    "start_mcp_server",
]
