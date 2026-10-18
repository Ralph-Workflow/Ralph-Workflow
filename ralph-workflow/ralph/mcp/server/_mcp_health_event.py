"""Bounded sink for MCP supervisor restart decisions and stage availability events.

LR (lifecycle owner) calls :func:`record_restart_decision` at each restart
decision; incident triage reads ``_DECISIONS`` to recover the exact sequence
of restart reasons, process exit statuses, probe durations, and log tails
the supervisor observed.

A separate :func:`record_stage` API records per-stage available/unavailable
verdicts the supervisor emitted (the original R3/R4 sink).

Durability: the sink is mirrored to a JSONL file when one is configured
via :func:`set_durable_sink_path`. Each decision is appended as a single
JSON line so an operator can read the last N decisions without any
in-memory access to the running process. Evidence is retained across bridge
shutdown — :func:`configure_bridge_sink` assigns each bridge a unique file
and never deletes or overwrites another bridge's evidence. The bounded
in-memory deque remains capped at 64 entries (oldest evicted on overflow).
When no sink path is set, the deque is the only store and the historical
log is reported as ``reason="unavailable"`` on read — the supervisor records
this fact explicitly so the operator can tell "no log was found" from
"log was captured and is empty".
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ralph.agents.invoke._watch_capacity import (
    _read_inotify_watch_limit,
    _read_inotify_watch_user_total,
    call_within_budget,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# bounded-accumulator-ok: cap=64
_DECISIONS: deque[RestartDecision] = deque(maxlen=64)

# Lock guarding the durable-sink file append. The supervisor calls
# ``record_restart_decision`` from a single thread (the health-check
# thread) so the lock is technically unnecessary, but the bounded sink
# is also exposed for tests that drive the API from multiple threads
# to verify the contract.
_DURABLE_SINK_LOCK: threading.Lock = threading.Lock()
_DURABLE_SINK_HOLDER: list[Path | None] = [None]


class RestartReason(Enum):
    """The root cause that triggered a restart decision."""

    PROCESS_EXITED = "process_exited"
    PROBE_FAILED = "probe_failed"


@dataclass(frozen=True)
class RestartDecision:
    """One restart decision with full evidence for incident triage.

    Attributes
    ----------
    reason : RestartReason
        One of ``PROCESS_EXITED`` or ``PROBE_FAILED``.
    exit_status : int | None
        The raw process exit code, present only when ``reason`` is
        ``PROCESS_EXITED``.
    probe_duration_s : float | None
        The wall-clock duration of the failed probe, present only when
        ``reason`` is ``PROBE_FAILED``.
    log_tail : tuple[str, ...] | None
        The last N log lines captured at the time of the decision, or
        ``None`` when no log is available.
    stage : str
        The startup stage or the string ``"process_exited"`` /
        ``"probe_failed"`` that was current when the decision was made.
    available : bool
        Always ``False`` for a restart decision record.
    """

    reason: RestartReason
    exit_status: int | None = None
    probe_duration_s: float | None = None
    log_tail: tuple[str, ...] | None = None
    stage: str = ""
    available: bool = False

    def __post_init__(self) -> None:
        if self.reason is RestartReason.PROCESS_EXITED and self.exit_status is None:
            raise ValueError("exit_status is required when reason is PROCESS_EXITED")
        if self.reason is RestartReason.PROBE_FAILED and self.probe_duration_s is None:
            raise ValueError("probe_duration_s is required when reason is PROBE_FAILED")


#: Maximum number of log lines stored per restart decision.
_LOG_TAIL_LINES = 10


def set_durable_sink_path(path: Path | None) -> None:
    """Configure the durable JSONL sink for restart decisions.

    Passing ``None`` clears the sink (decisions still land in the bounded
    in-memory deque so the supervisor's own logic keeps working). Passing
    a path causes every subsequent :func:`record_restart_decision` call
    to also append a JSON line to that file. The parent directory is
    created on first write, not on this call, so a misconfigured path
    only surfaces when a real decision is recorded.
    """
    _DURABLE_SINK_HOLDER[0] = path


def get_durable_sink_path() -> Path | None:
    """Return the currently configured durable sink path, or ``None``."""
    return _DURABLE_SINK_HOLDER[0]


def record_restart_decision(
    *,
    reason: RestartReason,
    exit_status: int | None = None,
    probe_duration_s: float | None = None,
    log_tail: tuple[str, ...] | None = None,
    stage: str,
    sink_path: Path | None = None,
) -> None:
    """Append a fully-evidenced restart decision to ``_DECISIONS``.

    Parameters
    ----------
    reason : RestartReason
        ``PROCESS_EXITED`` when the child process exited; ``PROBE_FAILED``
        when the health probe failed.
    exit_status : int | None
        Raw process exit code. Required when ``reason`` is ``PROCESS_EXITED``.
    probe_duration_s : float | None
        Wall-clock seconds the probe ran before failing. Required when
        ``reason`` is ``PROBE_FAILED``.
    log_tail : tuple[str, ...] | None
        Last ``_LOG_TAIL_LINES`` log lines at decision time, or ``None``
        when no log is available. The recorded value is ``"unavailable"``
        only when the API caller explicitly marks it so; a missing
        argument stays ``None`` to preserve the documented dataclass
        contract.
    stage : str
        The startup stage or health-state label active when the
        decision was made.
    sink_path : Path | None
        Explicit durable sink target. When ``None`` the module-global
        sink configured via :func:`set_durable_sink_path` is used, so
        existing callers keep working; bridge-scoped callers pass the
        bridge's own ``_durable_sink_path`` so records land only in
        that bridge's file regardless of the global setting.
    """
    decision = RestartDecision(
        reason=reason,
        exit_status=exit_status,
        probe_duration_s=probe_duration_s,
        log_tail=log_tail,
        stage=stage,
    )
    _DECISIONS.append(decision)
    # Mirror to the durable sink when one is configured. Errors are
    # swallowed: the bounded in-memory deque is the authoritative
    # record for the running supervisor and a failed disk append
    # must not stop the supervisor from recording this restart.
    target = sink_path if sink_path is not None else _DURABLE_SINK_HOLDER[0]
    if target is not None:
        try:
            with _DURABLE_SINK_LOCK:
                target.parent.mkdir(parents=True, exist_ok=True)
                with (
                    target.open(  # filesystem-write-ok: append-only durable JSONL stream; each restart decision must append, never rewrite history
                        "a", encoding="utf-8"
                    ) as fh
                ):
                    record: dict[str, object] = {
                        "reason": decision.reason.value,
                        "exit_status": decision.exit_status,
                        "probe_duration_s": decision.probe_duration_s,
                        # ``None`` (log unavailable) serialises as JSON null so an
                        # operator can tell it apart from ``[]`` (log captured, empty).
                        "log_tail": (
                            list(decision.log_tail) if decision.log_tail is not None else None
                        ),
                        "stage": decision.stage,
                    }
                    fh.write(json.dumps(record))
                    fh.write("\n")
        except OSError:
            pass


#: Bounded stage-availability event sink (the original R3/R4 API).
# bounded-accumulator-ok: cap=64
_EVENTS: deque[tuple[str, bool]] = deque(maxlen=64)


def record_stage(stage: str, *, available: bool) -> None:
    """Append an immutable ``(stage, available)`` record to ``_EVENTS``."""
    _EVENTS.append((stage, available))


def get_restart_decisions() -> tuple[RestartDecision, ...]:
    """Return the current sequence of restart decisions as an immutable snapshot."""
    return tuple(_DECISIONS)


def read_durable_sink(path: Path | None = None) -> tuple[dict[str, object], ...]:
    """Read the durable JSONL sink and return its lines as parsed dicts.

    When ``path`` is ``None`` the currently configured
    :func:`get_durable_sink_path` is used. When the file is missing or
    empty, an empty tuple is returned. When the file exists but cannot
    be parsed, a single-element tuple with
    ``{"reason": "unavailable", "stage": "..."}`` is returned so the
    operator can distinguish "no log" from "log captured and empty".
    """
    sink_path = path if path is not None else _DURABLE_SINK_HOLDER[0]
    if sink_path is None or not sink_path.exists():
        return ()
    try:
        lines: list[str] = []
        with sink_path.open(encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                lines.append(line)
    except OSError as exc:
        return ({"reason": "unavailable", "stage": str(exc)},)
    parsed: list[dict[str, object]] = []
    for line in lines:
        try:
            obj: object = json.loads(line)
        except json.JSONDecodeError:
            return ({"reason": "unavailable", "stage": "json_decode_failed"},)
        if not isinstance(obj, dict):
            return ({"reason": "unavailable", "stage": "json_decode_failed"},)
        parsed.append(obj)
    return tuple(parsed)


def clear_durable_sink(path: Path | None = None) -> None:
    """Remove the durable sink file, if any.

    Used by ``_McpHealthEventSink.reset()`` so a freshly constructed
    bridge starts with an empty incident log. No-op when the file is
    absent. Errors are swallowed: a failed cleanup of a sink file
    must not crash the bridge constructor.
    """
    sink_path = path if path is not None else _DURABLE_SINK_HOLDER[0]
    if sink_path is None:
        return
    try:
        sink_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def configure_bridge_sink(root: Path, bridge_id: str) -> Path:
    """Configure a unique durable sink for a specific bridge.

    Assigns ``bridge_id`` a unique file at
    ``root / ".agent" / f"mcp-health-events-{safe_id}.jsonl"`` where
    ``safe_id`` is the ``bridge_id`` with any character not in
    ``[A-Za-z0-9_-]`` replaced by the first 16 hex digits of its SHA-256
    hash (so every bridge id maps to a valid filename). Sets the global
    sink path to this file and returns it. No file is created eagerly;
    the parent directory is created on first write by
    :func:`record_restart_decision`. This function does NOT clear or delete
    any existing sink file — evidence written by a previous bridge is
    preserved.

    Parameters
    ----------
    root : Path
        Root directory that will contain the ``.agent`` subdirectory.
    bridge_id : str
        Unique identifier for the bridge. Non-safe characters trigger
        the hash fallback but never cause an error.

    Returns
    -------
    Path
        The configured sink path. Use ``read_durable_sink(path=...)``
        to read it back independently of the global singleton.
    """
    if re.fullmatch(r"[A-Za-z0-9_-]+", bridge_id):
        safe_id = bridge_id
    else:
        safe_id = hashlib.sha256(bridge_id.encode("utf-8")).hexdigest()[:16]
    sink_path = root / ".agent" / f"mcp-health-events-{safe_id}.jsonl"
    set_durable_sink_path(sink_path)
    return sink_path


#: Module-level FIFO for cross-process host-contention signals. Workspace
#: monitor pushes (timestamp, reason) on watch-budget warnings; bridge
#: pops the recent window. Capped so the deque never grows under load.
# bounded-accumulator-ok: cap=64
_HOST_CONTENTION_SIGNALS: deque[tuple[float, str]] = deque(maxlen=64)

#: Default ratio at or above which the current host is considered
#: contended. 0.85 = 85% of the inotify watch budget consumed. Bound
#: strictly below 1.0 so a saturated-but-not-fully-over host still
#: registers as contended.
CONTENDED_WATCH_RATIO: float = 0.85

# Import-time invariant
if not (0.0 < CONTENDED_WATCH_RATIO < 1.0):
    raise RuntimeError(f"CONTENDED_WATCH_RATIO must be in (0.0, 1.0) (got {CONTENDED_WATCH_RATIO})")


def record_host_contention_signal(reason: str) -> None:
    """Append (now, reason) to the contention-signal deque."""
    _HOST_CONTENTION_SIGNALS.append((time.monotonic(), reason))


def pop_recent_host_contention_signals(window_s: float) -> tuple[tuple[float, str], ...]:
    """Return all signals whose timestamp is within ``window_s`` of now.

    Signals are NOT removed — the deque is bounded at 64 entries. The
    bridge consults the WINDOW, not the deque length.
    """
    now = time.monotonic()
    return tuple((ts, reason) for ts, reason in _HOST_CONTENTION_SIGNALS if now - ts <= window_s)


def clear_host_contention_signals() -> None:
    """Clear the host contention signal deque."""
    _HOST_CONTENTION_SIGNALS.clear()


_clear_host_contention_signals = clear_host_contention_signals


def current_host_is_contended(
    *,
    budget_seconds: float = 1.0,
    watch_limit_reader: Callable[[], int | None] | None = None,
    watch_user_total_reader: Callable[[], int | None] | None = None,
) -> bool:
    """Return whether the host is currently under watch-budget pressure.

    Bounded — the underlying kernel data reads run via
    :func:`ralph.agents.invoke._watch_capacity.call_within_budget` with
    a default 1.0s budget. A blocked reader (e.g. uninterruptible
    sleep on a /proc/<pid>/fdinfo scan) times out and returns False
    within the budget; the bridge is NEVER held while /proc is read.
    """
    limit_fn = watch_limit_reader if watch_limit_reader is not None else _read_inotify_watch_limit
    user_total_fn = (
        watch_user_total_reader
        if watch_user_total_reader is not None
        else _read_inotify_watch_user_total
    )

    def _probe() -> bool:
        limit = limit_fn()
        if limit is None or limit <= 0:
            return False
        total = user_total_fn()
        if total is None:
            return False
        return (total / limit) >= CONTENDED_WATCH_RATIO

    return call_within_budget(_probe, fallback=False, budget_seconds=budget_seconds)


__all__ = [
    "CONTENDED_WATCH_RATIO",
    "RestartDecision",
    "RestartReason",
    "clear_durable_sink",
    "configure_bridge_sink",
    "current_host_is_contended",
    "get_durable_sink_path",
    "get_restart_decisions",
    "pop_recent_host_contention_signals",
    "read_durable_sink",
    "record_host_contention_signal",
    "record_restart_decision",
    "record_stage",
    "set_durable_sink_path",
]
