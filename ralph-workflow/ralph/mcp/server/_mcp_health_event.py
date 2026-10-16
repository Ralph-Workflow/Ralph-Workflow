"""Bounded sink for MCP supervisor restart decisions and stage availability events.

LR (lifecycle owner) calls :func:`record_restart_decision` at each restart
decision; incident triage reads ``_DECISIONS`` to recover the exact sequence
of restart reasons, process exit statuses, probe durations, and log tails
the supervisor observed.

A separate :func:`record_stage` API records per-stage available/unavailable
verdicts the supervisor emitted (the original R3/R4 sink).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

# bounded-accumulator-ok: cap=64
_DECISIONS: deque[RestartDecision] = deque(maxlen=64)


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
        when no log is available.
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


#: Bounded stage-availability event sink (the original R3/R4 API).
# bounded-accumulator-ok: cap=64
_EVENTS: deque[tuple[str, bool]] = deque(maxlen=64)


def record_stage(stage: str, *, available: bool) -> None:
    """Append an immutable ``(stage, available)`` record to ``_EVENTS``."""
    _EVENTS.append((stage, available))


def get_restart_decisions() -> tuple[RestartDecision, ...]:
    """Return the current sequence of restart decisions as an immutable snapshot."""
    return tuple(_DECISIONS)


__all__ = [
    "RestartDecision",
    "RestartReason",
    "get_restart_decisions",
    "record_restart_decision",
    "record_stage",
]
