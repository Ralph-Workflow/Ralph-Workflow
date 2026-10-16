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
in-memory access to the running process. The file is lifecycle-bounded
(an operator clears it when a new bridge starts) and rewritten atomically
on every append. When no sink path is set, the bounded in-memory deque
is the only store and the historical log is reported as
``reason="unavailable"`` on read — the supervisor records this fact
explicitly so the operator can tell "no log was found" from "log was
captured and is empty".
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# bounded-accumulator-ok: cap=64
_DECISIONS: deque[RestartDecision] = deque(maxlen=64)

# Lock guarding the durable-sink file append. The supervisor calls
# ``record_restart_decision`` from a single thread (the health-check
# thread) so the lock is technically unnecessary, but the bounded sink
# is also exposed for tests that drive the API from multiple threads
# to verify the contract.
_DURABLE_SINK_LOCK: threading.Lock = threading.Lock()
_DURABLE_SINK_PATH: Path | None = None


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
    global _DURABLE_SINK_PATH  # noqa: PLW0603 — intentional module-state singleton for the sink path
    _DURABLE_SINK_PATH = path


def get_durable_sink_path() -> Path | None:
    """Return the currently configured durable sink path, or ``None``."""
    return _DURABLE_SINK_PATH


def record_restart_decision(
    *,
    reason: RestartReason,
    exit_status: int | None = None,
    probe_duration_s: float | None = None,
    log_tail: tuple[str, ...] | None = None,
    stage: str,
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
    if _DURABLE_SINK_PATH is not None:
        try:
            with _DURABLE_SINK_LOCK:
                _DURABLE_SINK_PATH.parent.mkdir(parents=True, exist_ok=True)
                with (
                    _DURABLE_SINK_PATH.open(  # filesystem-write-ok: append-only durable JSONL stream; each restart decision must append, never rewrite history
                        "a", encoding="utf-8"
                    ) as fh
                ):
                    record: dict[str, object] = {
                        "reason": decision.reason.value,
                        "exit_status": decision.exit_status,
                        "probe_duration_s": decision.probe_duration_s,
                        "log_tail": list(decision.log_tail or ()),
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
    sink_path = path if path is not None else _DURABLE_SINK_PATH
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
    sink_path = path if path is not None else _DURABLE_SINK_PATH
    if sink_path is None:
        return
    try:
        sink_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


__all__ = [
    "RestartDecision",
    "RestartReason",
    "clear_durable_sink",
    "get_durable_sink_path",
    "get_restart_decisions",
    "read_durable_sink",
    "record_restart_decision",
    "record_stage",
    "set_durable_sink_path",
]
