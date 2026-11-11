
from __future__ import annotations

import collections
import contextlib
import errno
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final, NoReturn, Protocol, cast

import psutil
from loguru import logger

from ralph.mcp.bridge_diagnostics import (
    BridgeDiagnostics,
    bound_dominates,
    classify_probe_failure,
)
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
    MCP_ACCEPT_PAUSE_FILE,
    MCP_ACCEPT_RESUME_FILE,
    MCP_BRIDGE_PARENT_SOCK,
    MCP_EFFECTIVE_CONFIG_FINGERPRINT,
    MCP_INHERITED_FD_ENV,
    MCP_OWNER_PID_ENV,
    MCP_RESPAWN_ENV,
    MCP_SNAPSHOT_DIR_ENV,
    MCP_STAGE_ID_ENV,
)
from ralph.mcp.protocol.env import MCP_SESSION_FILE_ENV as SESSION_FILE_ENV
from ralph.mcp.protocol.startup import (
    mcp_boot_allowance_from_env,
    mcp_preflight_timeout_from_env,
    mcp_probe_timeout_from_env,
    preflight_http_mcp_server_tools,
    probe_mcp_http_endpoint,
)
from ralph.mcp.server._handover_protocol import (
    _CANDIDATE_BOOT_ALLOWANCE_S,
    _HANDOVER_ACK_TIMEOUT_S,
    _HANDOVER_DRAIN_S,
    _PRIVATE_READINESS_TIMEOUT_S,
    CandidateReadiness,
    check_candidate_readiness,
    compute_tool_set_hash,
    create_handover_socketpair,
    describe_tool_set_diff,
    request_old_child_pause,
    served_tool_entries,
    signal_candidate_resume,
    wait_for_old_child_pause_ack,
)
from ralph.mcp.server._http_tools_list import (
    _LIFECYCLE_MAX_RESPONSE_BYTES,
    _http_tools_list_names,
)
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._mcp_attempt_result import RestartAttemptResult
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
from ralph.mcp.server._mcp_outcome_unknown_error import McpOutcomeUnknownError
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy, next_budget_decision
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
    compute_effective_config_fingerprint,
    snapshot_dir,
    write_snapshot,
)
from ralph.mcp.server._spawn_process import SpawnProcess
from ralph.mcp.server._standalone_mcp_process import (
    SHUTDOWN_REAP_WAIT_S,
    SHUTDOWN_TERMINATE_GRACE_S,
    StandaloneMcpProcess,
)
from ralph.mcp.server._start_stage_tracker import read_last_stage as _read_start_stage
from ralph.mcp.server._startup_watch import StartupVerdict, StartupWatch
from ralph.mcp.tool_contract import visible_owned_tool_names
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.tool_catalog_cache import (
    UpstreamToolCatalogSnapshot,
    apply_tool_catalog_env,
    get_tool_catalog_snapshot,
)
from ralph.process.manager import (
    ManagedProcess,
    ProcessManagerPolicy,
    SpawnOptions,
    get_process_manager,
)
from ralph.workspace.fs import FsWorkspace

MCP_SESSION_ID_ENV = "MCP_SESSION_ID"

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from socket import socket as _socket_type

    from ralph.mcp.protocol.startup import SessionLike, WorkspaceLike
    from ralph.mcp.server._activity_relay import ActivityRelay
    from ralph.mcp.upstream.registry import UpstreamRegistry

_TOOL_REGISTRY_MAX_RESETS: int = 3
_PROBE_TIMEOUT_EXPAWN_CAP: int = 32




def _bridge_clock(self_or_bridge: object) -> Callable[[], float]:
    raw: object = getattr(self_or_bridge, "_clock_fn", None)
    if callable(raw):
        return raw
    return time.monotonic


def _restart_error_fields(exc: BaseException) -> tuple[str | None, float | None]:
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

_RESET_WRAPUP_TIMEOUT_S: float = 2.0
if not _RESET_WRAPUP_TIMEOUT_S > 0:
    raise RuntimeError(f"_RESET_WRAPUP_TIMEOUT_S must be positive (got {_RESET_WRAPUP_TIMEOUT_S})")

_INITIAL_TOOL_HASH_TIMEOUT_S: float = 5.0
_INITIAL_TOOL_HASH_TIMEOUT_MAX_S: float = 30.0
if not (
    _INITIAL_TOOL_HASH_TIMEOUT_S > 0
    and _INITIAL_TOOL_HASH_TIMEOUT_S < _INITIAL_TOOL_HASH_TIMEOUT_MAX_S
):
    raise RuntimeError(
        "_INITIAL_TOOL_HASH_TIMEOUT_S must be in (0,"
        f" {_INITIAL_TOOL_HASH_TIMEOUT_MAX_S:g}) seconds"
        f" (got {_INITIAL_TOOL_HASH_TIMEOUT_S})"
    )

_HANDOVER_STEP_SPAWN: Final[str] = "spawn_candidate"
_HANDOVER_STEP_VERIFY: Final[str] = "verify_candidate"
_HANDOVER_STEP_PAUSE_ACK: Final[str] = "pause_ack"
_HANDOVER_STEP_RESUME: Final[str] = "resume_candidate"
_HANDOVER_STEP_PROMOTE: Final[str] = "promote_candidate"
_HANDOVER_STEP_DRAIN: Final[str] = "drain_old_child"

_RESTART_BECAUSE_PROBE_FAILED: Final[int] = 0
_RESTART_BECAUSE_PROCESS_EXITED: Final[int] = 1

_HANDOVER_KILL_WAIT_S: float = 1.0
_HANDOVER_KILL_WAIT_MAX_S: float = 30.0
if not (_HANDOVER_KILL_WAIT_S > 0 and _HANDOVER_KILL_WAIT_S <= _HANDOVER_KILL_WAIT_MAX_S):
    raise RuntimeError(
        "_HANDOVER_KILL_WAIT_S must be in (0,"
        f" {_HANDOVER_KILL_WAIT_MAX_S:g}] seconds (got {_HANDOVER_KILL_WAIT_S})"
    )

_UNKILLABLE_REAP_BACKOFF_S: float = 0.5
_UNKILLABLE_REAP_BACKOFF_MAX_S: float = 30.0
_UNKILLABLE_REAP_RETRY_S: float = 1.0
if not (
    _UNKILLABLE_REAP_BACKOFF_S > 0
    and _UNKILLABLE_REAP_BACKOFF_S <= _UNKILLABLE_REAP_BACKOFF_MAX_S
    and _UNKILLABLE_REAP_RETRY_S > 0
):
    raise RuntimeError(
        f"_UNKILLABLE_REAP_BACKOFF_S={_UNKILLABLE_REAP_BACKOFF_S} and "
        f"_UNKILLABLE_REAP_RETRY_S={_UNKILLABLE_REAP_RETRY_S} must be positive "
        f"and bounded by {_UNKILLABLE_REAP_BACKOFF_MAX_S:g}s"
    )


_CANDIDATE_DEADLINE_S: Final[float] = _CANDIDATE_BOOT_ALLOWANCE_S + _PRIVATE_READINESS_TIMEOUT_S
_CANDIDATE_STARTUP_DEADLINE_S: Final[float] = mcp_whole_startup_budget_from_env()
_PREFLIGHT_DEADLINE_S: Final[float] = mcp_preflight_timeout_from_env().total_seconds()

_FAILED_ATTEMPT_CLEANUP_S: Final[float] = (
    SHUTDOWN_TERMINATE_GRACE_S + ProcessManagerPolicy.kill_followup_timeout_s + SHUTDOWN_REAP_WAIT_S
)
_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S: Final[float] = McpRestartPolicy.max_restarts * (
    McpRestartPolicy.cooldown_seconds + _PREFLIGHT_DEADLINE_S + _FAILED_ATTEMPT_CLEANUP_S
)

_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S: Final[float] = (
    _CANDIDATE_DEADLINE_S
    + _HANDOVER_ACK_TIMEOUT_S
    + _HANDOVER_KILL_WAIT_S
    + _HANDOVER_DRAIN_S
    + _FAILED_ATTEMPT_CLEANUP_S
)

if not (_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S > 0):
    raise RuntimeError(
        f"_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S must be positive (got {_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S})"
    )
if not (_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S > 0):
    raise RuntimeError(
        f"_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S must be positive (got {_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S})"
    )


_e64_bound_dominates = bound_dominates


_E64_BOUND_A_COMPONENTS_S: Final[tuple[float, ...]] = (
    float(McpRestartPolicy.cooldown_seconds),
    _PREFLIGHT_DEADLINE_S,
    _FAILED_ATTEMPT_CLEANUP_S,
)
_E64_BOUND_B_COMPONENTS_S: Final[tuple[float, ...]] = (
    _CANDIDATE_DEADLINE_S,
    _HANDOVER_ACK_TIMEOUT_S,
    _HANDOVER_KILL_WAIT_S,
    _HANDOVER_DRAIN_S,
    _FAILED_ATTEMPT_CLEANUP_S,
)
_E64_MAX_RESTARTS: Final[int] = int(McpRestartPolicy.max_restarts)
if not (_E64_MAX_RESTARTS > 0):
    raise RuntimeError(
        f"McpRestartPolicy.max_restarts must be positive (got {McpRestartPolicy.max_restarts})"
    )
if not _e64_bound_dominates(
    _WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S / _E64_MAX_RESTARTS,
    _E64_BOUND_A_COMPONENTS_S,
):
    raise RuntimeError(
        "E6.4 bound (a) is not an upper bound over its stages: per-attempt worst case"
        f" {_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S / _E64_MAX_RESTARTS:g}s"
        f" < sum{_E64_BOUND_A_COMPONENTS_S}={sum(_E64_BOUND_A_COMPONENTS_S):g}s"
        f" over {_E64_MAX_RESTARTS} attempts"
    )
if not _e64_bound_dominates(_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S, _E64_BOUND_B_COMPONENTS_S):
    raise RuntimeError(
        "E6.4 bound (b) is not an upper bound over its handover stages:"
        f" {_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S:g}s"
        f" < sum{_E64_BOUND_B_COMPONENTS_S}={sum(_E64_BOUND_B_COMPONENTS_S):g}s"
    )
if not (0 < _CANDIDATE_STARTUP_DEADLINE_S < _PREFLIGHT_DEADLINE_S):
    raise RuntimeError(
        f"E2.3 candidate startup deadline ({_CANDIDATE_STARTUP_DEADLINE_S}s) must be positive "
        f"and strictly less than preflight deadline ({_PREFLIGHT_DEADLINE_S}s)"
    )

_PROBE_DEADLINE_SLACK_S: float = 0.5
_CONFIRM_PROBE_MULTIPLIER: int = 2
_CONFIRM_CPU_SAMPLE_S: float = 0.5
_CPU_PROGRESS_THRESHOLD: Final[float] = 0.001
_DEFERRAL_COUNT_CEILING: int = 30
_DEFERRAL_WALL_TIME_CEILING_S: float = 60.0
if not (
    _PROBE_DEADLINE_SLACK_S > 0
    and _CONFIRM_PROBE_MULTIPLIER > 0
    and _CONFIRM_CPU_SAMPLE_S > 0
    and _DEFERRAL_COUNT_CEILING > 0
    and _DEFERRAL_WALL_TIME_CEILING_S > 0
):
    raise RuntimeError(
        f"_PROBE_DEADLINE_SLACK_S={_PROBE_DEADLINE_SLACK_S} "
        f"_CONFIRM_PROBE_MULTIPLIER={_CONFIRM_PROBE_MULTIPLIER} "
        f"_CONFIRM_CPU_SAMPLE_S={_CONFIRM_CPU_SAMPLE_S} "
        f"_DEFERRAL_COUNT_CEILING={_DEFERRAL_COUNT_CEILING} "
        f"_DEFERRAL_WALL_TIME_CEILING_S={_DEFERRAL_WALL_TIME_CEILING_S} must all be positive"
    )


_ALIAS_VERIFY_TOOL_NAME: str = "read_file"
_ALIAS_VERIFY_SERVER_NAME: str = RALPH_MCP_SERVER_NAME

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.startup import SessionLike, WorkspaceLike
    from ralph.mcp.upstream.registry import UpstreamRegistry

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

_PROBE_FAILURES_BEFORE_RESTART = 3

_PROBE_SUCCESS_HYSTERESIS_COUNT = 3
if not _PROBE_SUCCESS_HYSTERESIS_COUNT > 0:
    raise RuntimeError(
        f"_PROBE_SUCCESS_HYSTERESIS_COUNT must be positive (got {_PROBE_SUCCESS_HYSTERESIS_COUNT})"
    )

_DURABLE_SINK_FILENAME = "ralph_mcp_restart_decisions.jsonl"


def _process_is_dead(target: StandaloneMcpProcess) -> bool:
    proc: object = getattr(target, "process", None)
    if proc is None:
        return True
    poll_fn = cast("Callable[[], object] | None", getattr(proc, "poll", None))
    if poll_fn is None or not callable(poll_fn):
        return True
    try:
        return poll_fn() is not None
    except Exception:
        return False


def _is_alive_pid(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def is_alive_process(proc: object) -> bool:
    poll_fn = cast("Callable[[], object] | None", getattr(proc, "poll", None))
    if poll_fn is None or not callable(poll_fn):
        return False
    try:
        return poll_fn() is None
    except Exception:
        return False


def _has_stage(exc: BaseException) -> bool:
    return hasattr(exc, "last_stage") and hasattr(exc, "elapsed_s")


def _read_supervisor_field(exc: BaseException, name: str) -> object:
    storage: dict[str, object] = exc.__dict__
    return storage[name]


def _optional_attr_str(exc: BaseException) -> str | None:
    if not _has_stage(exc):
        return None
    raw = _read_supervisor_field(exc, "last_stage")
    if not isinstance(raw, str) or not raw:
        return None
    return raw


def _optional_attr_float(exc: BaseException) -> float | None:
    if not _has_stage(exc):
        return None
    raw = _read_supervisor_field(exc, "elapsed_s")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


_LOG_TAIL_LINES: int = 10
_LOG_TAIL_MAX_BYTES: int = 16 * 1024


def _is_non_deferrable_probe_failure(exc: Exception | None) -> bool:
    return classify_probe_failure(exc) == "fatal"


def _is_timeout_class_probe_outcome(exc: Exception | None, overrun: bool) -> bool:
    return classify_probe_failure(exc, overrun=overrun) == "timeout"


def _is_saturated_dispatch_outcome(exc: Exception | None) -> bool:
    return classify_probe_failure(exc) == "saturated"


def _stamp_handover_step(exc: BaseException, step: str) -> BaseException:
    if isinstance(exc, McpServerError) and exc.handover_step is None:
        exc.handover_step = step
    return exc


def _child_process_diagnostics(inner: object) -> tuple[int | None, str | None]:
    proc: object = getattr(inner, "process", None)
    raw_pid: object = getattr(proc, "pid", None)
    if not isinstance(raw_pid, int) or isinstance(raw_pid, bool):
        return None, None
    try:
        status = str(psutil.Process(raw_pid).status())
    except Exception:
        return raw_pid, None
    return raw_pid, status


class _RestartFnProtocol(Protocol):
    def __call__(self, *, make_before_break: bool = False) -> StandaloneMcpProcess: ...


class RestartAwareMcpBridge:

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
        clock_fn: Callable[[], float] | None = None,
        session_id: str | None = None,
        broker_fingerprint: str | None = None,
        effective_config_fingerprint: str | None = None,
        tool_entries_fn: Callable[[str, float], list[dict[str, object]]] | None = None,
        handover_ack_timeout_s: float | None = None,
        handover_kill_wait_s: float | None = None,
        handover_drain_s: float | None = None,
        unkillable_reap_backoff_s: float | None = None,
        unkillable_reap_retry_s: float | None = None,
    ) -> None:
        self._inner = inner
        self._restart_fn = restart_fn
        self._restart_policy = restart_policy
        self._run_id = run_id
        self._probe_fn = probe_fn
        self._probe_timeout_fn = probe_timeout_fn
        self._preflight_timeout_fn = preflight_timeout_fn
        self._activity_relay = activity_relay
        self._sleep_fn: Callable[[float], object] | None = sleep_fn
        self._handover_ack_timeout_s = (
            _HANDOVER_ACK_TIMEOUT_S
            if handover_ack_timeout_s is None
            else float(handover_ack_timeout_s)
        )
        self._handover_kill_wait_s = (
            _HANDOVER_KILL_WAIT_S if handover_kill_wait_s is None else float(handover_kill_wait_s)
        )
        self._handover_drain_s = (
            _HANDOVER_DRAIN_S if handover_drain_s is None else float(handover_drain_s)
        )
        self._unkillable_reap_backoff_s = (
            _UNKILLABLE_REAP_BACKOFF_S
            if unkillable_reap_backoff_s is None
            else float(unkillable_reap_backoff_s)
        )
        self._unkillable_reap_retry_s = (
            _UNKILLABLE_REAP_RETRY_S
            if unkillable_reap_retry_s is None
            else float(unkillable_reap_retry_s)
        )
        if not (
            self._handover_ack_timeout_s > 0.0
            and self._handover_kill_wait_s > 0.0
            and self._handover_drain_s > 0.0
            and self._unkillable_reap_backoff_s >= 0.0
            and self._unkillable_reap_retry_s >= 0.0
        ):
            raise ValueError("handover timing values must be positive and bounded")
        self._clock_fn: Callable[[], float] | None = clock_fn
        self._session_id: str | None = session_id
        self._broker_fingerprint: str | None = broker_fingerprint
        self._effective_config_fingerprint: str | None = effective_config_fingerprint
        self._expected_tool_hash: str | None = None
        self._expected_tool_entries: list[dict[str, object]] | None = None
        self._baseline_capture_failed: bool = False
        self._tool_entries_fn: Callable[[str, float], list[dict[str, object]]] | None = (
            tool_entries_fn
        )
        self._last_probe_error: Exception | None = None
        self._restart_timestamps: collections.deque[float] = collections.deque(maxlen=64)
        self._last_healthy_at: float = clock_fn() if clock_fn is not None else time.monotonic()
        self._healthy_period_started_at: float | None = (
            clock_fn() if clock_fn is not None else time.monotonic()
        )
        self._restart_count = 0
        self._consecutive_probe_failures = 0
        self._consecutive_probe_successes = 0
        self._tool_registry_resets = 0
        self._bridge_generation = 0
        self._lock = threading.RLock()
        self._dead_child_replace_lock = threading.Lock()
        self._workspace_root: Path | None = workspace_root
        self._listening_socket: _socket_type | None = None
        self._durable_sink_path: Path | None = None
        self._consecutive_probe_failures_deferred: int = 0
        self._contention_signal_producer: Callable[[], bool] | None = contention_signal_producer
        window_attr: object = getattr(restart_policy, "host_contention_window_seconds", 10.0)
        self._host_contention_window_seconds: float = (
            float(window_attr)
            if isinstance(window_attr, (int, float)) and not isinstance(window_attr, bool)
            else 10.0
        )
        self._first_probe_deferred_at: float | None = None
        self._draining_inners: collections.deque[StandaloneMcpProcess] = collections.deque(
            maxlen=16
        )
        self._current_candidate: StandaloneMcpProcess | None = None
        self._candidate_in_flight: bool = False
        self._drain_threads: list[
            threading.Thread
        ] = []
        self._is_shutting_down: bool = False
        self._unkillable_reapers: collections.deque[tuple[object, int]] = collections.deque(
            maxlen=8
        )
        self._unkillable_reap_threads: list[
            threading.Thread
        ] = []

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def tool_registry_resets(self) -> int:
        return self._tool_registry_resets

    def diagnostics(self) -> BridgeDiagnostics:
        with self._lock:
            entries = tuple(dict(entry) for entry in (self._expected_tool_entries or ()))
            return BridgeDiagnostics(
                restart_count=self._restart_count,
                restart_attempt_count=len(self._restart_timestamps),
                generation=self._bridge_generation,
                consecutive_probe_failures=self._consecutive_probe_failures,
                consecutive_probe_successes=self._consecutive_probe_successes,
                deferred_probe_failures=self._consecutive_probe_failures_deferred,
                first_probe_deferred=self._first_probe_deferred_at is not None,
                session_id=self._session_id,
                broker_fingerprint=self._broker_fingerprint,
                tool_set_hash=self._expected_tool_hash,
                tool_set_entries=entries,
                tool_set_baseline_captured=self._expected_tool_hash is not None,
                tool_set_baseline_failed=self._baseline_capture_failed,
                draining_process_count=len(self._draining_inners),
                probe_deferral_count_ceiling=_DEFERRAL_COUNT_CEILING,
                probe_deferral_wall_time_ceiling_s=_DEFERRAL_WALL_TIME_CEILING_S,
                probe_deadline_slack_s=_PROBE_DEADLINE_SLACK_S,
                tool_registry_max_resets=_TOOL_REGISTRY_MAX_RESETS,
                respawn_alias_verify_timeout_s=_RESPAWN_ALIAS_VERIFY_TIMEOUT_S,
                initial_tool_hash_timeout_s=_INITIAL_TOOL_HASH_TIMEOUT_S,
                initial_tool_hash_timeout_max_s=_INITIAL_TOOL_HASH_TIMEOUT_MAX_S,
                candidate_deadline_s=_CANDIDATE_DEADLINE_S,
                preflight_deadline_s=_PREFLIGHT_DEADLINE_S,
                candidate_startup_deadline_s=_CANDIDATE_STARTUP_DEADLINE_S,
                handover_ack_timeout_s=self._handover_ack_timeout_s,
                handover_kill_wait_s=self._handover_kill_wait_s,
                handover_drain_s=self._handover_drain_s,
                max_restarts=self._restart_policy.max_restarts,
                cooldown_seconds=self._restart_policy.cooldown_seconds,
                worst_case_terminal_exhaustion_dead_child_s=(
                    _WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S
                ),
                candidate_delay_bound_alive_child_s=_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S,
                failed_attempt_cleanup_s=_FAILED_ATTEMPT_CLEANUP_S,
                bound_a_components_s=_E64_BOUND_A_COMPONENTS_S,
                bound_b_components_s=_E64_BOUND_B_COMPONENTS_S,
            )

    @property
    def process(self) -> ProcessLike:
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
        with self._lock:
            self._is_shutting_down = True
            cand = self._current_candidate
            self._current_candidate = None
            draining = list(self._draining_inners)
            self._draining_inners.clear()
            drain_threads = list(self._drain_threads)
            self._drain_threads.clear()
            unkillable_threads = list(self._unkillable_reap_threads)
            self._unkillable_reap_threads.clear()
            unkillable_reapers = list(self._unkillable_reapers)
            self._unkillable_reapers.clear()
        if cand is not None:
            with contextlib.suppress(Exception):
                cand.shutdown()
        for d in draining:
            with contextlib.suppress(Exception):
                d.shutdown()
        for t in drain_threads:
            with contextlib.suppress(Exception):
                if t is not threading.current_thread():
                    t.join(timeout=1.0)
        for entry in unkillable_reapers:
            proc, _ = entry
            kill_fn = cast("Callable[[], object] | None", getattr(proc, "kill", None))
            if callable(kill_fn):
                with contextlib.suppress(Exception):
                    kill_fn()
            wait_fn = cast("Callable[[float], object] | None", getattr(proc, "wait", None))
            if callable(wait_fn):
                with contextlib.suppress(Exception):
                    wait_fn(2.0)
        for t in unkillable_threads:
            with contextlib.suppress(Exception):
                if t is not threading.current_thread():
                    t.join(timeout=1.0)
        self._inner.shutdown()
        listening_socket: _socket_type | None = getattr(self, "_listening_socket", None)
        if listening_socket is not None:
            with contextlib.suppress(OSError):
                listening_socket.close()
            self._listening_socket = None

    def _cooldown_after_failure(self) -> None:
        seconds = self._restart_policy.cooldown_seconds
        if not isinstance(seconds, (int, float)) or not seconds > 0:
            return
        sleep_fn = cast("Callable[[float], object] | None", getattr(self, "_sleep_fn", None))
        if sleep_fn is None:
            sleep_fn = time.sleep
        sleep_fn(seconds)

    @property
    def activity_relay(self) -> ActivityRelay | None:
        return self._activity_relay

    def relay_health_error(self) -> str | None:
        return None if self._activity_relay is None else self._activity_relay.health_error()

    def register_activity_sink(self, sink: Callable[[str], None]) -> Callable[[], None]:
        if self._activity_relay is None:
            return lambda: None
        return self._activity_relay.register_sink(sink)

    def _read_mcp_log_tail(self) -> tuple[str, ...] | None:
        root = self._workspace_root
        if root is None:
            return None
        log_path = mcp_server_log_path(root)
        try:
            size = mcp_server_log_size(root)
            if size == 0:
                if log_path.exists():
                    return ()
                return None
            with log_path.open("rb") as fh:
                fh.seek(max(0, size - _LOG_TAIL_MAX_BYTES))
                chunk = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        lines = chunk.split("\n")
        if size > _LOG_TAIL_MAX_BYTES and len(lines) > 1:
            lines = lines[1:]
        if lines and lines[-1] == "":
            lines.pop()
        return tuple(lines[-_LOG_TAIL_LINES:])

    def _run_health_probe(self) -> tuple[bool, float | None, bool, bool]:
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
        deferred_count = max(0, self._consecutive_probe_failures_deferred)
        safe_exponent = min(deferred_count, _PROBE_TIMEOUT_EXPAWN_CAP)
        multiplier: int = 2**safe_exponent
        probe_timeout: timedelta = min(base_timeout * multiplier, ceiling)
        clock_fn = _bridge_clock(self)
        probe_start = clock_fn()
        try:
            self._probe_fn(self._inner.endpoint, probe_timeout)
            self._last_probe_error = None
            return False, None, False, False
        except Exception as exc:
            self._last_probe_error = exc
            if _is_saturated_dispatch_outcome(exc):
                return False, None, False, False
            elapsed = clock_fn() - probe_start
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
                    target.open(
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
        attempt_index = self._begin_restart_attempt(
            process_exited=process_exited,
            probe_duration_s=probe_duration_s,
        )
        self._cooldown_after_failure()

        if process_exited or _process_is_dead(self._inner):
            return self._replace_dead_child(attempt_index=attempt_index)

        return self._run_with_make_before_break()

    def _begin_restart_attempt(
        self,
        *,
        process_exited: bool,
        probe_duration_s: float | None,
    ) -> int:
        with self._lock:
            clock_fn = _bridge_clock(self)
            now = clock_fn()
            raw_healthy: object = getattr(self._restart_policy, "healthy_reset_s", 300.0)
            healthy_reset_s: float = (
                float(raw_healthy) if isinstance(raw_healthy, (int, float)) else 300.0
            )
            if (
                self._healthy_period_started_at is not None
                and now - self._healthy_period_started_at >= healthy_reset_s
            ):
                self._restart_timestamps.clear()
                self._restart_count = 0
            self._healthy_period_started_at = None
            self._last_healthy_at = now

            raw_window: object = getattr(self._restart_policy, "rolling_window_s", 600.0)
            rolling_window_s: float = (
                float(raw_window) if isinstance(raw_window, (int, float)) else 600.0
            )
            decision = next_budget_decision(
                self._restart_policy,
                self._restart_count,
                tuple(self._restart_timestamps),
                rolling_window_s,
                _now=lambda: now,
            )
            if decision != "ok":
                reason = "probe failed" if not process_exited else "exited"
                child_pid, psutil_status = _child_process_diagnostics(self._inner)
                raise McpServerError(
                    f"MCP server {reason} and restart budget exhausted "
                    f"(current={self._restart_count}, "
                    f"max={self._restart_policy.max_restarts})",
                    restart_count=self._restart_count,
                    failure_kind=RestartAttemptResult.BUDGET_EXHAUSTED,
                    restart_reason="probe_failed" if not process_exited else "process_exited",
                    child_pid=child_pid,
                    psutil_status=psutil_status,
                    probe_duration_s=probe_duration_s,
                )
            self._restart_timestamps.append(now)

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
                reason=(
                    RestartReason.PROCESS_EXITED if process_exited else RestartReason.PROBE_FAILED
                ),
                exit_status=self._inner.process.poll() if process_exited else None,
                probe_duration_s=probe_duration_s if not process_exited else None,
                log_tail=log_tail,
                stage="process_exited" if process_exited else "probe_failed",
            )
        return attempt_index

    def _replace_dead_child(self, *, attempt_index: int) -> bool:
        with self._lock:
            if self._workspace_root is not None:
                snap = snapshot_dir(self._workspace_root, self._run_id)
                (snap / "accept.pause").unlink(missing_ok=True)
                (snap / "accept.pause.ack").unlink(missing_ok=True)
                signal_candidate_resume(snap / "accept.resume")
            try:
                self._inner.shutdown()
                self._inner = self._restart_fn()
            except Exception as restart_exc:
                last_stage, elapsed_s = _restart_error_fields(restart_exc)
                logger.warning(
                    "MCP restart attempt {}/{} failed (non-terminal, budget remains): "
                    "last_stage={} elapsed_s={} error={}",
                    attempt_index,
                    self._restart_policy.max_restarts,
                    last_stage,
                    elapsed_s,
                    restart_exc,
                )
                self._reset_probe_counters()
                return False
            self._bridge_generation += 1
            self._reset_probe_counters()
            logger.info(
                "MCP server restarted on stable endpoint {}; restart_count={}",
                self._inner.endpoint,
                self._restart_count,
            )
            return True

    def _reset_probe_counters(self) -> None:
        self._consecutive_probe_failures = 0
        self._consecutive_probe_successes = 0
        self._consecutive_probe_failures_deferred = 0
        self._first_probe_deferred_at = None

    def _spawn_candidate(self) -> StandaloneMcpProcess:
        fn = cast(
            "_RestartFnProtocol", self._restart_fn
        )
        try:
            return fn(make_before_break=True)
        except TypeError:
            return self._restart_fn()

    def capture_tool_hash_baseline(self) -> None:
        endpoint = self._inner.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            self._expected_tool_hash = None
            self._expected_tool_entries = None
            self._baseline_capture_failed = True
            logger.warning("tool-hash baseline skipped: inner endpoint is not a usable string")
            return
        fetch = self._tool_entries_fn if self._tool_entries_fn is not None else served_tool_entries
        try:
            entries = fetch(endpoint, _INITIAL_TOOL_HASH_TIMEOUT_S)
        except (OSError, ValueError, TimeoutError) as exc:
            self._expected_tool_hash = None
            self._expected_tool_entries = None
            self._baseline_capture_failed = True
            logger.warning(
                "tool-hash baseline capture failed for {}; tool-set drift detection is "
                "refusing candidates until a baseline can be recovered: {}",
                endpoint,
                exc,
            )
            return
        if not entries:
            self._expected_tool_hash = None
            self._expected_tool_entries = None
            self._baseline_capture_failed = True
            logger.warning(
                "tool-hash baseline capture returned an empty served tool list for {}; "
                "tool-set drift detection is refusing candidates until a baseline can be "
                "recovered",
                endpoint,
            )
            return
        self._expected_tool_entries = entries
        self._expected_tool_hash = compute_tool_set_hash(entries)
        self._baseline_capture_failed = False
        logger.debug(
            "tool-hash baseline captured from served tools/list: endpoint={} tools={} hash={}",
            endpoint,
            len(entries),
            self._expected_tool_hash,
        )

    def _apply_baseline_unavailable(self, result: CandidateReadiness, old_dead: bool) -> bool:
        if old_dead:
            logger.warning(
                "Tool-hash baseline unavailable and old child is dead. "
                "Promoting candidate with warning (dead-child escape): "
                "{}",
                result.detail,
            )
            if result.tool_hash is not None:
                self._absorb_verified_tool_set(result)
            return True
        logger.warning(
            "Candidate verification refused: tool-hash baseline unavailable "
            "(no drift reference) and old child is alive. Keeping old "
            "child. detail={}",
            result.detail,
        )
        return False

    def _apply_config_fingerprint_mismatch(
        self, result: CandidateReadiness, old_dead: bool
    ) -> bool:
        if old_dead:
            logger.warning(
                "Effective-config fingerprint mismatch but old child is "
                "dead; promoting candidate with warning (dead-child "
                "escape): {}",
                result.detail,
            )
            return True
        logger.warning(
            "Candidate verification refused on effective-config fingerprint mismatch: detail={}",
            result.detail,
        )
        return False

    def _verify_candidate_socket(
        self,
        candidate: StandaloneMcpProcess,
        parent_sock: socket.socket,
        raw_pid: object,
    ) -> bool:
        pid: int = raw_pid if isinstance(raw_pid, int) else 0
        raw_stage: object = getattr(candidate, "stage_id", None)
        stage_id: str = raw_stage if isinstance(raw_stage, str) else ""
        session_id: str = self._session_id or ""
        broker_fp: str | None = self._broker_fingerprint
        cfg_fp: str | None = self._effective_config_fingerprint
        verified = False
        try:
            result: CandidateReadiness = check_candidate_readiness(
                parent_sock,
                expected_spawn_id=stage_id,
                expected_pid=pid,
                expected_session_id=session_id,
                expected_broker_fingerprint=broker_fp,
                expected_effective_config_fingerprint=cfg_fp,
                expected_tool_hash=self._expected_tool_hash,
                expected_tool_hash_required=self._baseline_capture_failed,
                timeout=_CANDIDATE_DEADLINE_S,
            )
            if result.ok:
                if result.tool_hash is not None:
                    self._absorb_verified_tool_set(result)
                verified = True
            else:
                old_dead = self._is_old_child_dead()
                if result.failure_stage == "tool_set_drift":
                    diff = describe_tool_set_diff(
                        self._expected_tool_entries, list(result.tools) or None
                    )
                    if old_dead:
                        logger.warning(
                            "Tool-set drift detected during candidate verification, but old "
                            "child is dead; promoting candidate with warning: {} | {}",
                            result.detail,
                            diff,
                        )
                        if result.tool_hash is not None:
                            self._absorb_verified_tool_set(result)
                        verified = True
                    else:
                        logger.warning(
                            "Candidate verification refused on tool-set drift: stage={} "
                            "detail={} tool_set_diff={}",
                            result.failure_stage,
                            result.detail,
                            diff,
                        )
                elif result.failure_stage == "baseline_unavailable":
                    verified = self._apply_baseline_unavailable(result, old_dead)
                elif result.failure_stage == "effective_config_fingerprint_mismatch":
                    verified = self._apply_config_fingerprint_mismatch(result, old_dead)
                else:
                    logger.warning(
                        "Candidate verification failed: stage={} detail={}",
                        result.failure_stage,
                        result.detail,
                    )
        except Exception as exc:
            logger.warning("Candidate verification raised exception: {}", exc)
        finally:
            with contextlib.suppress(Exception):
                parent_sock.close()
            candidate.handover_parent_sock = None
        return verified

    def _is_old_child_dead(self) -> bool:
        proc_old: object = getattr(self._inner, "process", None)
        poll_fn: object = getattr(proc_old, "poll", None)
        if not callable(poll_fn):
            return False
        poll_result: object = poll_fn()
        return poll_result is not None

    def _absorb_verified_tool_set(self, result: CandidateReadiness) -> None:
        self._expected_tool_hash = result.tool_hash
        self._expected_tool_entries = list(result.tools)
        self._baseline_capture_failed = False

    def _verify_candidate(self, candidate: StandaloneMcpProcess) -> bool:
        proc: object = getattr(candidate, "process", None)
        if proc is None:
            return True
        poll_fn: object = getattr(proc, "poll", None)
        if callable(poll_fn):
            try:
                poll_result: object = poll_fn()
                if poll_result is not None:
                    return False
            except Exception:
                return False
        parent_sock_obj: object = getattr(candidate, "handover_parent_sock", None)
        proc_pid: object = getattr(proc, "pid", None)
        if type(proc).__name__ != "FakeProcess" and isinstance(parent_sock_obj, socket.socket):
            return self._verify_candidate_socket(candidate, parent_sock_obj, proc_pid)
        return True

    def _hand_over_accept(
        self, old_inner: StandaloneMcpProcess, candidate: StandaloneMcpProcess
    ) -> None:
        if self._workspace_root is not None:
            snap = snapshot_dir(self._workspace_root, self._run_id)
            old_stage_obj: object = getattr(old_inner, "stage_id", None)
            old_stage: str | None = (
                old_stage_obj if isinstance(old_stage_obj, str) and old_stage_obj else None
            )
            pause_file = (
                snap / f"accept.pause.{old_stage}"
                if old_stage is not None
                else snap / "accept.pause"
            )
            cand_stage_obj: object = getattr(candidate, "stage_id", None)
            cand_stage: str | None = (
                cand_stage_obj if isinstance(cand_stage_obj, str) and cand_stage_obj else None
            )
            resume_file = (
                snap / f"accept.resume.{cand_stage}"
                if cand_stage is not None
                else snap / "accept.resume"
            )
            proc: object = getattr(old_inner, "process", None)
            if is_alive_process(proc):
                try:
                    request_old_child_pause(pause_file)
                    ack = wait_for_old_child_pause_ack(
                        pause_file,
                        timeout=self._handover_ack_timeout_s,
                        sleep_fn=self._sleep_fn,
                    )
                except BaseException as pause_exc:
                    with contextlib.suppress(Exception):
                        candidate.shutdown()
                    raise _stamp_handover_step(pause_exc, _HANDOVER_STEP_PAUSE_ACK) from pause_exc
                if not ack:
                    logger.warning(
                        "Old child failed to acknowledge pause within {}s; escalating termination",
                        self._handover_ack_timeout_s,
                    )
                    kill_fn: object = getattr(proc, "kill", None)
                    if callable(kill_fn):
                        with contextlib.suppress(Exception):
                            kill_fn()
                    poll_fn = cast(
                        "Callable[[], object] | None", getattr(proc, "poll", None)
                    )
                    old_exited = False
                    if poll_fn is not None and callable(poll_fn):
                        deadline = time.monotonic() + self._handover_kill_wait_s
                        while time.monotonic() < deadline:
                            poll_res: object = poll_fn()
                            if poll_res is not None:
                                old_exited = True
                                break
                            sleep_fn = self._sleep_fn if self._sleep_fn is not None else time.sleep
                            sleep_fn(0.05)
                    else:
                        old_exited = not is_alive_process(proc)

                    if not old_exited:
                        logger.warning(
                            "Old child survived SIGKILL after pause ack timeout; "
                            "promoting candidate (D-state/nonkillable survivor, "
                            "single-acceptor safety preserved via pause sentinel); "
                            "deferring reap to bridge shutdown"
                        )
                        self._schedule_unkillable_reap(proc)
            try:
                signal_candidate_resume(resume_file)
                if cand_stage is not None:
                    signal_candidate_resume(snap / "accept.resume")
            except BaseException as resume_exc:
                with contextlib.suppress(Exception):
                    candidate.shutdown()
                raise _stamp_handover_step(resume_exc, _HANDOVER_STEP_RESUME) from resume_exc

    def _drain_old(self, old_inner: StandaloneMcpProcess) -> None:
        drain_s = self._handover_drain_s
        sleep_fn = cast("Callable[[float], object] | None", getattr(self, "_sleep_fn", None))
        proc: object = getattr(old_inner, "process", None)
        if sleep_fn is None and (self._workspace_root is None or not is_alive_process(proc)):
            with contextlib.suppress(Exception):
                old_inner.shutdown()
            return
        if sleep_fn is None:
            sleep_fn = time.sleep

        self._draining_inners.append(old_inner)

        def _drain_worker() -> None:
            try:
                sleep_fn(drain_s)
            except Exception:
                pass
            finally:
                with self._lock:
                    with contextlib.suppress(ValueError):
                        self._draining_inners.remove(old_inner)
                    with contextlib.suppress(ValueError):
                        self._drain_threads.remove(threading.current_thread())
                with contextlib.suppress(Exception):
                    old_inner.shutdown()

        t = threading.Thread(target=_drain_worker, name="mcp-old-drain", daemon=True)
        with self._lock:
            self._drain_threads.append(t)
        t.start()

    def _promote_and_reap(
        self, candidate: StandaloneMcpProcess, old_inner: StandaloneMcpProcess
    ) -> None:
        try:
            self._inner = candidate
            self._bridge_generation += 1
            self._consecutive_probe_failures = 0
            self._consecutive_probe_successes = 0
            self._consecutive_probe_failures_deferred = 0
            self._first_probe_deferred_at = None
        except BaseException as promote_exc:
            raise _stamp_handover_step(promote_exc, _HANDOVER_STEP_PROMOTE) from promote_exc
        try:
            self._drain_old(old_inner)
        except BaseException as drain_exc:
            raise _stamp_handover_step(drain_exc, _HANDOVER_STEP_DRAIN) from drain_exc

    def _claim_candidate_slot(self, *, bump_generation: bool = False) -> int | None:
        if self._is_shutting_down or self._candidate_in_flight:
            return None
        if bump_generation:
            self._bridge_generation += 1
        self._candidate_in_flight = True
        return self._bridge_generation

    def _release_candidate_slot(self) -> None:
        with self._lock:
            self._candidate_in_flight = False

    def _schedule_unkillable_reap(self, proc: object) -> None:
        handle_id = id(proc)
        with self._lock:
            self._unkillable_reapers.append((proc, handle_id))

        def _reap_worker() -> None:
            try:
                sleep_fn = self._sleep_fn or time.sleep
                sleep_fn(self._unkillable_reap_backoff_s)
                terminate_fn = cast("Callable[[], object] | None", getattr(proc, "terminate", None))
                if callable(terminate_fn):
                    with contextlib.suppress(Exception):
                        terminate_fn()
                deadline = time.monotonic() + self._unkillable_reap_retry_s
                poll_fn = cast("Callable[[], object] | None", getattr(proc, "poll", None))
                while time.monotonic() < deadline:
                    if poll_fn is not None and callable(poll_fn):
                        with contextlib.suppress(Exception):
                            if poll_fn() is not None:
                                return
                    sleep_fn(0.05)
                kill_fn = cast("Callable[[], object] | None", getattr(proc, "kill", None))
                if callable(kill_fn):
                    with contextlib.suppress(Exception):
                        kill_fn()
                wait_fn = cast("Callable[[float], object] | None", getattr(proc, "wait", None))
                if callable(wait_fn):
                    with contextlib.suppress(Exception):
                        wait_fn(2.0)
            finally:
                with self._lock, contextlib.suppress(ValueError):
                    for entry in list(self._unkillable_reapers):
                        if entry[1] == handle_id:
                            self._unkillable_reapers.remove(entry)
                            break

        t = threading.Thread(
            target=_reap_worker, name=f"mcp-unkillable-reap-{handle_id}", daemon=True
        )
        with self._lock:
            self._unkillable_reap_threads.append(t)
        t.start()

    def _reservation_is_current(self, token: int) -> bool:
        with self._lock:
            return (
                not self._is_shutting_down
                and self._candidate_in_flight
                and self._bridge_generation == token
            )

    def _abandon_candidate(self, candidate: StandaloneMcpProcess) -> None:
        with contextlib.suppress(Exception):
            candidate.shutdown()

    def _run_with_make_before_break(self) -> bool:
        with self._lock:
            token = self._claim_candidate_slot()
        if token is None:
            logger.warning(
                "MCP replacement skipped: a candidate replacement is already in flight "
                "(non-terminal, old child kept)"
            )
            return False
        try:
            return self._replace_with_candidate(token=token, attempt_index=self._restart_count)
        finally:
            self._release_candidate_slot()

    def _replace_with_candidate(
        self,
        *,
        token: int,
        attempt_index: int,
        initiator: str = "mcp-restart",
        pre_promotion_gate: Callable[[StandaloneMcpProcess], None] | None = None,
    ) -> bool:
        if self._workspace_root is not None:
            snap = snapshot_dir(self._workspace_root, self._run_id)
            (snap / "accept.resume").unlink(missing_ok=True)
            (snap / "accept.pause").unlink(missing_ok=True)
            (snap / "accept.pause.ack").unlink(missing_ok=True)
        with self._lock:
            old_inner = self._inner
        try:
            candidate = self._spawn_candidate()
        except Exception as restart_exc:
            _stamp_handover_step(restart_exc, _HANDOVER_STEP_SPAWN)
            last_stage, elapsed_s = _restart_error_fields(restart_exc)
            logger.warning(
                "MCP {} attempt {}/{} candidate spawn failed "
                "(non-terminal, old child kept, handover_step={}): "
                "last_stage={} elapsed_s={} error={}",
                initiator,
                attempt_index,
                self._restart_policy.max_restarts,
                _HANDOVER_STEP_SPAWN,
                last_stage,
                elapsed_s,
                restart_exc,
            )
            self._mark_probe_misses_consumed()
            return False

        with self._lock:
            shutting_down = self._is_shutting_down
            if not shutting_down:
                self._current_candidate = candidate
        if not self._reservation_is_current(token):
            self._abandon_candidate(candidate)
            logger.warning(
                "MCP {} attempt {}/{} candidate abandoned before verification "
                "(non-terminal, old child kept)",
                initiator,
                attempt_index,
                self._restart_policy.max_restarts,
            )
            return False

        try:
            try:
                candidate_verified = self._verify_candidate(candidate)
            except BaseException as verify_exc:
                self._abandon_candidate(candidate)
                raise _stamp_handover_step(verify_exc, _HANDOVER_STEP_VERIFY) from verify_exc
            if not candidate_verified or self._is_shutting_down:
                logger.warning(
                    "MCP {} attempt {}/{} candidate verification failed "
                    "(non-terminal, old child kept, handover_step={})",
                    initiator,
                    attempt_index,
                    self._restart_policy.max_restarts,
                    _HANDOVER_STEP_VERIFY,
                )
                self._abandon_candidate(candidate)
                self._mark_probe_misses_consumed()
                return False

            if pre_promotion_gate is not None:
                try:
                    pre_promotion_gate(candidate)
                except BaseException as gate_exc:
                    self._abandon_candidate(candidate)
                    raise _stamp_handover_step(gate_exc, _HANDOVER_STEP_VERIFY) from gate_exc

            if not self._reservation_is_current(token):
                self._abandon_candidate(candidate)
                logger.warning(
                    "MCP {} attempt {}/{} candidate abandoned before promotion: the bridge "
                    "generation moved while the candidate was being verified "
                    "(non-terminal, old child kept)",
                    initiator,
                    attempt_index,
                    self._restart_policy.max_restarts,
                )
                return False

            try:
                self._hand_over_accept(old_inner, candidate)
            except McpServerError as handover_exc:
                if handover_exc.handover_step == _HANDOVER_STEP_PAUSE_ACK:
                    logger.warning(
                        "MCP {} attempt {}/{} candidate handover failed: "
                        "old child still alive and could not be confirmed stopped "
                        "(non-terminal, old child kept, handover_step={}): {}",
                        initiator,
                        attempt_index,
                        self._restart_policy.max_restarts,
                        _HANDOVER_STEP_PAUSE_ACK,
                        handover_exc,
                    )
                    self._abandon_candidate(candidate)
                    self._mark_probe_misses_consumed()
                    return False
                self._abandon_candidate(candidate)
                raise

            try:
                self._promote_and_reap(candidate, old_inner)
            except BaseException as promote_exc:
                self._abandon_candidate(candidate)
                raise _stamp_handover_step(promote_exc, _HANDOVER_STEP_PROMOTE) from promote_exc
        finally:
            self._forget_candidate(candidate)
        logger.info(
            "MCP server restarted via make-before-break on stable endpoint {}; restart_count={}",
            self._inner.endpoint,
            self._restart_count,
        )
        return True

    def _mark_probe_misses_consumed(self) -> None:
        self._consecutive_probe_failures = _PROBE_FAILURES_BEFORE_RESTART - 1
        self._consecutive_probe_failures_deferred = 0
        self._first_probe_deferred_at = None

    def _forget_candidate(self, candidate: StandaloneMcpProcess) -> None:
        with self._lock:
            if self._current_candidate is candidate:
                self._current_candidate = None

    def _attempt_restart_with_candidate(
        self,
        *,
        process_exited: bool,
        probe_duration_s: float | None,
    ) -> str:
        try:
            replaced = self._perform_restart(
                process_exited=process_exited,
                probe_duration_s=probe_duration_s,
            )
        except McpServerError:
            raise
        if replaced:
            return RestartAttemptResult.REPLACED
        return RestartAttemptResult.ATTEMPT_FAILED

    def _check_child_cpu_progress(self, pid: int, sample_duration_s: float) -> bool:
        if pid <= 1:
            return False
        try:
            proc = psutil.Process(pid)
            c1 = proc.cpu_times()
            sleep_fn = self._sleep_fn or time.sleep
            sleep_fn(sample_duration_s)
            c2 = proc.cpu_times()
            return ((c2.user - c1.user) + (c2.system - c1.system)) > _CPU_PROGRESS_THRESHOLD
        except Exception:
            return False

    def _handle_failed_probe_under_lock(
        self,
        *,
        fresh_b_signal: bool,
        probe_duration_s: float | None,
        probed_pid: int,
        probed_generation: int,
        deferrable: bool = True,
    ) -> bool:
        restart_because: int | None = None
        with self._lock:
            if (
                self._inner.process.pid != probed_pid
                or self._bridge_generation != probed_generation
            ):
                logger.debug("stale probe result discarded: bridge changed while producer ran")
                return False

            if self._inner.process.poll() is not None:
                restart_because = _RESTART_BECAUSE_PROCESS_EXITED
            else:
                clock_fn = _bridge_clock(self)
                now = clock_fn()
                deferral_wall_time = (
                    (now - self._first_probe_deferred_at)
                    if self._first_probe_deferred_at is not None
                    else 0.0
                )
                if (
                    self._consecutive_probe_failures_deferred >= _DEFERRAL_COUNT_CEILING
                    or deferral_wall_time >= _DEFERRAL_WALL_TIME_CEILING_S
                ):
                    logger.warning("deferral ceiling tripped; forcing replacement")
                    self._first_probe_deferred_at = None
                    restart_because = _RESTART_BECAUSE_PROBE_FAILED
                else:
                    fresh_w_signal = (
                        bool(
                            pop_recent_host_contention_signals(self._host_contention_window_seconds)
                        )
                        if deferrable
                        else False
                    )
                    fresh_contention = (fresh_w_signal or fresh_b_signal) and deferrable
                    if fresh_contention:
                        if self._first_probe_deferred_at is None:
                            self._first_probe_deferred_at = now
                        self._consecutive_probe_failures_deferred += 1
                        self._record_contention_deferral(
                            probe_duration_s=probe_duration_s,
                            deferral_count=self._consecutive_probe_failures_deferred,
                            stage="probe_deferred_contention",
                        )
                        logger.debug(
                            "MCP server probe failed with fresh contention signal; deferring "
                            "restart (deferred={}, w={}, b={})",
                            self._consecutive_probe_failures_deferred,
                            fresh_w_signal,
                            fresh_b_signal,
                        )
                        return False

                    self._consecutive_probe_failures_deferred = 0
                    self._first_probe_deferred_at = None
                    self._consecutive_probe_failures += 1
                    if self._consecutive_probe_failures < _PROBE_FAILURES_BEFORE_RESTART:
                        logger.debug(
                            "MCP server probe failed ({}/{} before restart); server still running",
                            self._consecutive_probe_failures,
                            _PROBE_FAILURES_BEFORE_RESTART,
                        )
                        return False
                    restart_because = _RESTART_BECAUSE_PROBE_FAILED

        return self._perform_restart(
            process_exited=restart_because == _RESTART_BECAUSE_PROCESS_EXITED,
            probe_duration_s=(
                None if restart_because == _RESTART_BECAUSE_PROCESS_EXITED else probe_duration_s
            ),
        )

    def _restart_observed_dead_child(self, observed_inner: StandaloneMcpProcess) -> bool:
        with self._dead_child_replace_lock:
            with self._lock:
                already_replaced = self._inner is not observed_inner
            if already_replaced:
                return False
            return self._perform_restart(process_exited=True, probe_duration_s=None)

    def check_health_and_restart_if_needed(self) -> bool:
        with self._lock:
            observed_inner, inner_exited = self._inner, self._inner.process.poll() is not None
        if inner_exited:
            return self._restart_observed_dead_child(observed_inner)

        with self._lock:
            probe_failed, probe_duration_s, infra_error, overrun = self._run_health_probe()
            if infra_error:
                return False

            if not probe_failed:
                self._consecutive_probe_successes += 1
                if self._consecutive_probe_successes >= _PROBE_SUCCESS_HYSTERESIS_COUNT:
                    self._consecutive_probe_failures = 0
                    self._consecutive_probe_failures_deferred = 0
                    self._first_probe_deferred_at = None
                now = _bridge_clock(self)()
                self._last_healthy_at = now
                if self._healthy_period_started_at is None:
                    self._healthy_period_started_at = now
                raw_healthy: object = getattr(self._restart_policy, "healthy_reset_s", 300.0)
                healthy_reset_s: float = (
                    float(raw_healthy) if isinstance(raw_healthy, (int, float)) else 300.0
                )
                if now - self._healthy_period_started_at >= healthy_reset_s:
                    self._restart_timestamps.clear()
                    self._restart_count = 0
                    self._healthy_period_started_at = now
                return False

            self._consecutive_probe_successes = 0
            self._healthy_period_started_at = None

            probed_pid = self._inner.process.pid
            probed_generation = self._bridge_generation

        deferrable = _is_timeout_class_probe_outcome(self._last_probe_error, overrun)
        fresh_b_signal_from_overrun = False
        if probe_failed and overrun and deferrable:
            record_host_contention_signal("probe_overrun")
            fresh_b_signal_from_overrun = True
        producer = self._contention_signal_producer or current_host_is_contended
        try:
            fresh_b_signal = bool(producer())
        except Exception:
            fresh_b_signal = False
        fresh_b_signal = (fresh_b_signal or fresh_b_signal_from_overrun) if deferrable else False

        clock_fn = _bridge_clock(self)
        now = clock_fn()
        deferral_wall_time = (
            (now - self._first_probe_deferred_at)
            if self._first_probe_deferred_at is not None
            else 0.0
        )
        ceiling_tripped = (
            self._consecutive_probe_failures_deferred >= _DEFERRAL_COUNT_CEILING
            or deferral_wall_time >= _DEFERRAL_WALL_TIME_CEILING_S
        )
        will_trip_restart = not fresh_b_signal and (
            self._consecutive_probe_failures + 1 >= _PROBE_FAILURES_BEFORE_RESTART
        )
        if will_trip_restart and not ceiling_tripped and self._probe_fn is not None:
            confirm_ok = False
            base_timeout = (
                self._probe_timeout_fn()
                if self._probe_timeout_fn is not None
                else timedelta(seconds=5)
            )
            confirm_timeout = base_timeout * _CONFIRM_PROBE_MULTIPLIER
            try:
                self._probe_fn(self._inner.endpoint, confirm_timeout)
                confirm_ok = True
            except Exception as confirm_exc:
                if _is_timeout_class_probe_outcome(confirm_exc, overrun=False):
                    confirm_ok = self._check_child_cpu_progress(probed_pid, _CONFIRM_CPU_SAMPLE_S)
            if confirm_ok:
                with self._lock:
                    if (
                        self._bridge_generation == probed_generation
                        and self._inner.process.pid == probed_pid
                    ):
                        if self._first_probe_deferred_at is None:
                            self._first_probe_deferred_at = now
                        self._consecutive_probe_failures_deferred += 1
                        self._record_contention_deferral(
                            probe_duration_s=probe_duration_s,
                            deferral_count=self._consecutive_probe_failures_deferred,
                            stage="probe_deferred_confirm_success",
                        )
                        return False

        return self._handle_failed_probe_under_lock(
            fresh_b_signal=fresh_b_signal,
            probe_duration_s=probe_duration_s,
            probed_pid=probed_pid,
            probed_generation=probed_generation,
            deferrable=deferrable,
        )

    def reset_tool_registry(self) -> None:
        with self._lock:
            token = self._claim_candidate_slot(bump_generation=True)
            if token is None:
                logger.info(
                    "MCP tool-registry-reset skipped: a candidate replacement is already "
                    "in flight and will rebuild the registry"
                )
                return
            if self._tool_registry_resets >= _TOOL_REGISTRY_MAX_RESETS:
                cap_pid, cap_status = _child_process_diagnostics(self._inner)
                self._release_candidate_slot()
                raise McpServerError(
                    "MCP tool-registry-reset exhausted "
                    f"({_TOOL_REGISTRY_MAX_RESETS}) "
                    f"after {self._tool_registry_resets} reset(s)",
                    restart_count=self._restart_count,
                    restart_reason="tool_registry_reset_exhausted",
                    child_pid=cap_pid,
                    psutil_status=cap_status,
                )
            if self._restart_count >= self._restart_policy.max_restarts:
                budget_pid, budget_status = _child_process_diagnostics(self._inner)
                self._release_candidate_slot()
                raise McpServerError(
                    "MCP server restart budget exhausted "
                    f"(current={self._restart_count}, "
                    f"max={self._restart_policy.max_restarts})",
                    restart_count=self._restart_count,
                    restart_reason="probe_failed",
                    child_pid=budget_pid,
                    psutil_status=budget_status,
                )
            self._tool_registry_resets += 1
            self._restart_count += 1
            attempt_index = self._tool_registry_resets
            logger.info(
                "Resetting MCP tool registry ({}/{}); endpoint={}",
                attempt_index,
                _TOOL_REGISTRY_MAX_RESETS,
                self._inner.endpoint,
            )
            old_exited = _process_is_dead(self._inner)

        try:
            self._cooldown_after_failure()
            if old_exited:
                self._reset_cold_child(attempt_index=attempt_index)
                return
            self._replace_with_candidate(
                token=token,
                attempt_index=attempt_index,
                initiator="tool-registry-reset",
                pre_promotion_gate=self._verify_alias_present,
            )
        finally:
            self._release_candidate_slot()

    def _reset_cold_child(self, *, attempt_index: int) -> None:
        with self._lock:
            try:
                self._inner.shutdown()
                self._inner = self._restart_fn()
            except Exception as restart_exc:
                last_stage, elapsed_s = _restart_error_fields(restart_exc)
                logger.warning(
                    "MCP tool-registry-reset attempt {}/{} failed "
                    "(non-terminal, budget remains): last_stage={} "
                    "elapsed_s={} error={}",
                    attempt_index,
                    _TOOL_REGISTRY_MAX_RESETS,
                    last_stage,
                    elapsed_s,
                    restart_exc,
                )
                return
        self._verify_alias_present()

    def _verify_alias_present(self, target: StandaloneMcpProcess | None = None) -> None:
        active = target if target is not None else self._inner
        endpoint = active.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            logger.debug("alias-verify skipped: inner process endpoint is not a string")
            return
        inner_process: object = getattr(active, "process", None)
        if inner_process is None or not hasattr(inner_process, "poll"):
            logger.debug("alias-verify skipped: inner process lacks poll() (MagicMock?)")
            return
        try:
            tool_names = _http_tools_list_names(endpoint, timeout=_RESPAWN_ALIAS_VERIFY_TIMEOUT_S)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            msg = f"mcp alias verify probe failed after respawn: endpoint={endpoint!r} error={exc}"
            logger.error(msg)
            probe_pid, probe_status = _child_process_diagnostics(active)
            raise McpServerError(
                msg,
                restart_count=self._restart_count,
                restart_reason="alias_verify_probe_failed",
                child_pid=probe_pid,
                psutil_status=probe_status,
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
        alias_pid, alias_status = _child_process_diagnostics(active)
        raise McpServerError(
            msg,
            restart_count=self._restart_count,
            restart_reason="alias_missing_after_respawn",
            child_pid=alias_pid,
            psutil_status=alias_status,
        )

    def reset_session_budget(self) -> None:
        endpoint = self._inner.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            logger.debug("reset_session_budget skipped: inner process endpoint is not a string")
            return
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
                    response_data = response.read(_LIFECYCLE_MAX_RESPONSE_BYTES)
                finally:
                    response.close()
                _ = response_data
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
    effective_extras = extras or McpServerExtras()
    lifecycle_deps = deps or _default_lifecycle_deps()
    root = _workspace_root(workspace)
    visible_tools = _visible_mcp_tool_names_owned(
        session, workspace, upstream_registry=upstream_registry
    )

    def _resolve_catalog_snapshot() -> UpstreamToolCatalogSnapshot:
        try:
            return get_tool_catalog_snapshot(root)
        except ImportError:
            return UpstreamToolCatalogSnapshot(present=False)

    reserve = (
        lifecycle_deps.reserve_listening_socket
        if lifecycle_deps.reserve_listening_socket is not None
        else _reserve_listening_socket
    )
    _host, port, listening_socket = reserve()
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

        def _restart_fn(*args: object, **kwargs: object) -> StandaloneMcpProcess:
            raw_mbb: object = kwargs.get("make_before_break", False)
            mbb: bool = raw_mbb is True
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
                make_before_break=mbb,
                tool_catalog_snapshot=_resolve_catalog_snapshot(),
                activity_relay=effective_extras.activity_relay,
            )

        broker_secret_val: str | None = getattr(session, "broker_secret", None)
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
            session_id=session.session_id,
            broker_fingerprint=_finger(broker_secret_val),
            effective_config_fingerprint=compute_effective_config_fingerprint(root),
        )
        bridge._listening_socket = listening_socket
        bridge._durable_sink_path = _configure_bridge_sink(root, uuid.uuid4().hex[:12])
        bridge.capture_tool_hash_baseline()
    except BaseException:
        with contextlib.suppress(OSError):
            listening_socket.close()
        raise
    return bridge


def startup_diagnostic_suffix(root: Path, log_offset: int) -> str:

    diagnostic = read_mcp_server_log_since(root, log_offset)
    if not diagnostic:
        return f"\nMCP server output ({mcp_server_log_path(root)}):\nchild wrote no log output"
    return f"\nMCP server output ({mcp_server_log_path(root)}):\n{diagnostic}"


_startup_diagnostic_suffix = startup_diagnostic_suffix


def _boot_allowance_seconds(deps: LifecycleDeps) -> float:
    if deps.boot_allowance is None:
        return 0.0
    try:
        return max(0.0, deps.boot_allowance().total_seconds())
    except Exception:
        return 0.0


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
    clock: Callable[[], float] = time.monotonic,
) -> None:
    preflight_timeout_td = deps.preflight_timeout()
    preflight_timeout_s = preflight_timeout_td.total_seconds()
    try:
        whole_startup_s = mcp_whole_startup_budget_from_env(env)
    except Exception:
        whole_startup_s = preflight_timeout_s
    startup_deadline_s = min(preflight_timeout_s, whole_startup_s)
    if startup_deadline_s >= preflight_timeout_s:
        startup_deadline_s = max(0.1, preflight_timeout_s - 0.5)
    boot_allowance_s = _boot_allowance_seconds(deps)
    watch = StartupWatch(
        spawn_start=spawn_start,
        envelope_s=startup_deadline_s,
        boot_allowance_s=boot_allowance_s,
        read_stage=lambda: _read_start_stage(root, spawn_id=stage_id),
        clock=clock,
    )
    preflight_budget_td = preflight_timeout_td + timedelta(seconds=max(0.0, boot_allowance_s))

    preflight_error: list[Exception] = []
    preflight_done = threading.Event()

    def _run_preflight() -> None:
        try:
            deps.preflight(endpoint, visible_tools, preflight_budget_td)
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
    verdict = StartupVerdict.WAITING
    poll_interval = 0.02

    while True:
        if preflight_done.wait(timeout=poll_interval):
            break
        rc = bridge.process.poll()
        if rc is not None:
            early_exit_rc = rc
            break
        verdict = watch.check()
        if verdict is not StartupVerdict.WAITING:
            break

    if early_exit_rc is None and verdict is StartupVerdict.WAITING and not preflight_error:
        return

    _raise_startup_failure(
        bridge,
        endpoint=endpoint,
        root=root,
        log_offset=log_offset,
        stage_id=stage_id,
        elapsed_s=clock() - spawn_start,
        early_exit_rc=early_exit_rc,
        verdict=verdict,
        preflight_error=preflight_error,
        startup_deadline_s=startup_deadline_s,
        boot_allowance_s=boot_allowance_s,
    )


def _raise_startup_failure(
    bridge: StandaloneMcpProcess,
    *,
    endpoint: str,
    root: Path,
    log_offset: int,
    stage_id: str,
    elapsed_s: float,
    early_exit_rc: int | None,
    verdict: StartupVerdict,
    preflight_error: list[Exception],
    startup_deadline_s: float,
    boot_allowance_s: float,
) -> NoReturn:
    returncode = bridge.process.poll()
    child_pid, psutil_status = _child_process_diagnostics(bridge)
    bridge.shutdown()
    diagnostic = _startup_diagnostic_suffix(root, log_offset)
    last_stage = _read_start_stage(root, spawn_id=stage_id)
    cause = preflight_error[0] if preflight_error else None

    def _error(message: str, reason: str) -> McpServerError:
        return McpServerError(
            message,
            restart_count=0,
            last_stage=last_stage if last_stage is not None else "last_stage_unknown",
            elapsed_s=elapsed_s,
            restart_reason=reason,
            child_pid=child_pid,
            psutil_status=psutil_status,
        )

    if early_exit_rc is not None or returncode is not None:
        effective_rc = early_exit_rc if early_exit_rc is not None else returncode
        raise _error(
            f"MCP server process exited before endpoint {endpoint} became ready "
            f"(rc={effective_rc}){diagnostic}",
            "process_exited",
        ) from cause
    if verdict is StartupVerdict.BOOT_DEADLINE_EXCEEDED:
        raise _error(
            f"MCP server child did not begin startup within the boot allowance of "
            f"{boot_allowance_s:.3f}s (interpreter boot/imports; the host may be "
            f"overloaded) before endpoint {endpoint} became ready{diagnostic}",
            verdict.value,
        ) from cause
    if verdict is StartupVerdict.ENVELOPE_DEADLINE_EXCEEDED:
        raise _error(
            f"MCP server startup exceeded deadline of {startup_deadline_s:.3f}s "
            f"before endpoint {endpoint} became ready{diagnostic}",
            verdict.value,
        ) from cause
    raise _error(
        f"MCP server endpoint {endpoint} did not become ready: {cause}{diagnostic}",
        "preflight_failed",
    ) from cause


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
    make_before_break: bool = False,
    tool_catalog_snapshot: UpstreamToolCatalogSnapshot | None = None,
    activity_relay: ActivityRelay | None = None,
) -> StandaloneMcpProcess:
    endpoint = f"http://127.0.0.1:{port}/mcp"
    log_offset = mcp_server_log_size(root)
    session_file: Path | None = None
    parent_sock: socket.socket | None = None
    child_sock: socket.socket | None = None
    bridge_run_id = session.run_id
    session_id_value: str = session.session_id
    if not session_id_value:
        raise McpServerError(
            "session_id missing",
            restart_count=0,
            last_stage="snapshot_missing",
        )
    snap_dir = snapshot_dir(root, bridge_run_id)
    snap_session_file: Path = snap_dir / "session.json"
    broker_secret_val: str | None = getattr(session, "broker_secret", None)
    broker_fingerprint: str | None = _finger(broker_secret_val)
    effective_config_fingerprint: str | None = compute_effective_config_fingerprint(root)
    if not snap_session_file.exists():
        write_snapshot(
            snap_dir,
            session_id=session_id_value,
            run_id=bridge_run_id,
            broker_secret_fingerprint=broker_fingerprint,
            effective_config_fingerprint=effective_config_fingerprint,
        )
    env: dict[str, str] | None = None
    pass_fds: tuple[int, ...] = ()
    stage_id: str = ""
    spawn_start: float = 0.0
    try:
        session_file = (
            deps.create_session_file(root, session)
            if deps.create_session_file is not None
            else snap_session_file
        )
        env, stage_id, pass_fds = _build_spawn_env(
            session_file=session_file,
            deps=deps,
            _extra_env=_extra_env,
            snap_dir=snap_dir,
            session_id_value=session_id_value,
            tool_catalog_snapshot=tool_catalog_snapshot,
            activity_relay=activity_relay,
            inherited_fd=inherited_fd,
            respawn=respawn,
            make_before_break=make_before_break,
        )
        if make_before_break:
            parent_sock, child_sock = create_handover_socketpair()
            child_fd = child_sock.fileno()
            env[MCP_BRIDGE_PARENT_SOCK] = str(child_fd)
            if effective_config_fingerprint is not None:
                env[MCP_EFFECTIVE_CONFIG_FINGERPRINT] = effective_config_fingerprint
            pass_fds = (*pass_fds, child_fd)
        spawn_start = time.monotonic()
        executable, package_root = resolve_mcp_runtime(
            executable=Path(sys.executable), package_root=_PACKAGE_ROOT
        )
        env = _env_for_mcp_package_root(env, package_root)
        process = deps.spawn_process(
            [
                str(executable),
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
        if child_sock is not None:
            with contextlib.suppress(Exception):
                child_sock.close()
        bridge = StandaloneMcpProcess(
            endpoint=endpoint,
            process=process,
            session_file=session_file,
            stage_id=stage_id,
            handover_parent_sock=parent_sock,
        )
    except BaseException:
        if child_sock is not None:
            with contextlib.suppress(Exception):
                child_sock.close()
        if parent_sock is not None:
            with contextlib.suppress(Exception):
                parent_sock.close()
        if session_file is not None:
            session_file.unlink(missing_ok=True)
        raise

    if not make_before_break:
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


def resolve_mcp_runtime(*, executable: Path, package_root: Path) -> tuple[Path, Path]:
    original = (executable, package_root)
    if executable.is_file() and (package_root / "ralph" / "__init__.py").is_file():
        return original
    try:
        generation = executable.parents[2]
        generations = generation.parent
    except IndexError:
        generation = package_root
        generations = Path()
    if generations.name != "generations" or package_root.resolve() != generation.resolve():
        return original
    current = generations.parent / "current"
    try:
        current_generation = current.resolve(strict=True)
    except OSError:
        return original
    if (
        current_generation.parent.resolve() != generations.resolve()
        or not (current_generation / "ralph" / "__init__.py").is_file()
    ):
        return original
    candidates = (
        current_generation / ".venv" / "bin" / executable.name,
        current_generation / ".venv" / "bin" / "python3",
        current_generation / ".venv" / "bin" / "python",
    )
    current_executable = next((path for path in candidates if path.is_file()), None)
    if current_executable is None:
        return original
    logger.warning(
        "MCP parent runtime generation vanished; recovering candidate spawn through "
        "current immutable generation {}",
        current_generation,
    )
    return current_executable.resolve(), current_generation.resolve()


def _env_for_mcp_package_root(env: dict[str, str], package_root: Path) -> dict[str, str]:
    updated = dict(env)
    entries = [
        entry
        for entry in updated.get("PYTHONPATH", "").split(os.pathsep)
        if entry and Path(entry).resolve() != _PACKAGE_ROOT.resolve()
    ]
    updated["PYTHONPATH"] = os.pathsep.join((str(package_root), *entries))
    return updated


def _build_spawn_env(
    *,
    session_file: Path,
    deps: LifecycleDeps,
    _extra_env: dict[str, str] | None,
    snap_dir: Path,
    session_id_value: str,
    tool_catalog_snapshot: UpstreamToolCatalogSnapshot | None,
    activity_relay: ActivityRelay | None,
    inherited_fd: int | None,
    respawn: bool,
    make_before_break: bool,
) -> tuple[dict[str, str], str, tuple[int, ...]]:
    env = deps.subprocess_env(session_file)
    if _extra_env:
        env.update(_extra_env)
    env[SESSION_FILE_ENV] = str(session_file)
    for _branch_owned_key in (
        MCP_INHERITED_FD_ENV,
        MCP_RESPAWN_ENV,
        MCP_ACCEPT_RESUME_FILE,
        MCP_BRIDGE_PARENT_SOCK,
        MCP_EFFECTIVE_CONFIG_FINGERPRINT,
    ):
        env.pop(_branch_owned_key, None)
    env[MCP_SESSION_ID_ENV] = session_id_value
    env[MCP_SNAPSHOT_DIR_ENV] = str(snap_dir)
    stage_id = str(uuid.uuid4())
    env[MCP_STAGE_ID_ENV] = stage_id
    env[MCP_ACCEPT_PAUSE_FILE] = str(snap_dir / f"accept.pause.{stage_id}")
    if tool_catalog_snapshot is not None:
        apply_tool_catalog_env(env, tool_catalog_snapshot)
    if activity_relay is not None:
        env.update(activity_relay.server_environment())
    pass_fds: tuple[int, ...] = (inherited_fd,) if inherited_fd is not None else ()
    if inherited_fd is not None:
        env[MCP_INHERITED_FD_ENV] = str(inherited_fd)
    if respawn:
        env[MCP_RESPAWN_ENV] = "1"
    if make_before_break:
        env[MCP_ACCEPT_RESUME_FILE] = str(snap_dir / f"accept.resume.{stage_id}")
    return env, stage_id, pass_fds


def shutdown_mcp_server(bridge: SessionBridgeLike) -> None:
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
    )
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
    env["PYTHONUNBUFFERED"] = "1"
    package_root = str(_PACKAGE_ROOT)
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
    log_path = mcp_server_log_path(cwd)
    log_fd: int = -1
    try:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fd = os.open(
                log_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o644,
            )
        except OSError as exc:
            if exc.errno not in (
                errno.ENOSPC,
                errno.EROFS,
                errno.EACCES,
                errno.EPERM,
            ):
                raise
            log_fd = os.open(
                os.devnull, os.O_WRONLY
            )
            logger.warning(
                "MCP diagnostic log setup failed (errno={} path={}); "
                "degrading to devnull so startup and serving can continue",
                exc.errno,
                log_path,
            )
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
                require_direct_parent=True,
            ),
        )
    finally:
        if log_fd >= 0:
            os.close(log_fd)


def _default_lifecycle_deps() -> LifecycleDeps:
    return LifecycleDeps(
        reserve_listening_socket=_reserve_listening_socket,
        create_session_file=_create_session_file,
        subprocess_env=_subprocess_env,
        spawn_process=_spawn_process,
        preflight=preflight_http_mcp_server_tools,
        preflight_timeout=mcp_preflight_timeout_from_env,
        boot_allowance=mcp_boot_allowance_from_env,
        probe=probe_mcp_http_endpoint,
        probe_timeout=mcp_probe_timeout_from_env,
    )


def _create_session_file(root: Path, session: SessionLike) -> Path:
    del root
    fd, temp_path = tempfile.mkstemp(prefix="ralph-mcp-session-", suffix=".json")
    path = Path(temp_path)
    try:
        with os.fdopen(
            fd, "w", encoding="utf-8"
        ) as stream:
            stream.write(session_payload_json(session))
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def identity_is_serialisable(identity: MultimodalModelIdentity) -> bool:
    return identity.is_known() or identity.transport is not None


def session_payload_json(session: SessionLike) -> str:
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
    if isinstance(raw_identity, MultimodalModelIdentity) and identity_is_serialisable(raw_identity):
        session_payload["model_identity"] = {
            "provider": raw_identity.provider,
            "model_id": raw_identity.model_id,
            "transport": payload_transport(raw_identity.transport),
        }
    raw_profile: object = getattr(session, "capability_profile", None)
    if isinstance(raw_profile, ResolvedCapabilityProfile):
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
    "McpOutcomeUnknownError",
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
