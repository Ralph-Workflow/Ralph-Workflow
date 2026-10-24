"""Integration adversity catalogue: named fault kinds and their classifications.

Every member of :class:`FaultKind` names ONE failure mode of the
integration adversity catalogue (the rows A-F of the product request) and
every member is REGISTERED in the catalogue registry. A registration says:

* the catalogue ROW the member belongs to;
* the PHASE of the simulated environment that consumes it;
* the EFFECT that phase has (refuse the mutation, fail it, recover from
  it, defer it, crash before or after it, or do nothing);
* the typed OUTCOME the member produces -- one of ``succeeded``,
  ``definitely_failed``, ``timed_out``, ``killed`` or ``unknown``;
* the user-facing REASON the simulated environment states;
* whether a resolver AGENT may be dispatched for it (only a real content
  conflict may dispatch one);
* whether the LOCAL landing still succeeds despite the fault;
* whether the INTEGRATION flow can reach the member at all.

The registry is the single source of truth for the catalogue: the simulated
environment states the registered reason, and the catalogue tests assert the
registered reason. A member that is not registered cannot be classified into
a typed outcome, so the completeness guard in
``tests/test_integration_fault_catalogue.py`` fails on it -- adding a fault
kind without classifying it can never pass silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "CATALOGUE_ROWS",
    "FaultEffect",
    "FaultKind",
    "FaultOutcome",
    "FaultPhase",
    "catalogue",
    "catalogue_rows",
    "classification_for",
    "registered_outcome",
]

#: The catalogue rows of the product request. Every row carries at least one
#: named fault kind in the registry.
CATALOGUE_ROWS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F")

#: The typed outcome of one external call. Mirrors the product's typed
#: outcome set: a caller matches on the kind, never on message text.
FaultOutcome = Literal["succeeded", "definitely_failed", "timed_out", "killed", "unknown"]

#: The phase of the simulated environment that consumes a fault kind.
FaultPhase = Literal["rebase", "landing", "remote", "observation", "shutdown", "any"]

#: What the consuming phase does with the fault.
FaultEffect = Literal[
    "refuse",
    "fail",
    "recover",
    "defer",
    "concurrent_advance",
    "crash_before",
    "crash_after",
    "noop",
]


class FaultKind(Enum):
    """Named failure modes from the integration adversity catalogue.

    Each member names one catalogue row (A-F) and is registered in
    :func:`catalogue`. The groups below follow the catalogue rows.
    """

    # -- Row A: a Git command misbehaves --------------------------------
    GIT_TIMEOUT = auto()
    GIT_TIMEOUT_PARTIAL_OUTPUT = auto()
    GIT_IGNORES_SIGTERM = auto()
    GIT_KILLED_MID_WRITE = auto()
    LOCK_CONTENTION = auto()
    STALE_LOCK = auto()
    LIVE_FOREIGN_LOCK = auto()
    AUTO_GC_CONCURRENT = auto()
    HOOK_PROMPT_HANG = auto()
    INTERACTIVE_PROMPT = auto()
    REAL_CONFLICT = auto()

    # -- Row B: machine and filesystem ----------------------------------
    DISK_FULL = auto()
    INODE_EXHAUSTION = auto()
    READ_ONLY_FILESYSTEM = auto()
    PERMISSION_DENIED = auto()
    HOST_OVERLOAD = auto()
    WALL_CLOCK_JUMP = auto()
    CRASH_BEFORE_STEP = auto()
    CRASH_AFTER_STEP = auto()
    WORKTREE_DELETED = auto()

    # -- Row C: remote and network --------------------------------------
    REMOTE_OUTAGE = auto()
    REMOTE_DNS_FAILURE = auto()
    REMOTE_TLS_FAILURE = auto()
    REMOTE_RATE_LIMITED = auto()
    REMOTE_PUSH_REJECTED = auto()
    REMOTE_DIVERGED = auto()
    REMOTE_FETCH_ONLY = auto()

    # -- Row D: repository topology -------------------------------------
    TARGET_CHECKED_OUT_ELSEWHERE = auto()
    HEAD_DETACHED = auto()
    CONCURRENT_TARGET_ADVANCE = auto()
    RECORD_UNREADABLE = auto()
    FOREIGN_OPERATION = auto()

    # -- Row E: agents and background work ------------------------------
    AGENT_ACTIVE = auto()
    AGENT_LEFT_LOCK = auto()
    AGENT_NO_CHANGE = auto()
    BACKGROUND_TICKER_OVERLAP = auto()

    # -- Row F: observability and shutdown ------------------------------
    STALL_WATCHDOG = auto()
    SHUTDOWN_STALL = auto()
    REPORT_VALIDATION_FAILURE = auto()

    # -- Control: the no-fault baseline ---------------------------------
    NONE = auto()


@dataclass(frozen=True)
class _FaultClassification:
    """How one fault kind behaves inside the simulated environment."""

    kind: FaultKind
    row: str
    phase: FaultPhase
    effect: FaultEffect
    outcome: FaultOutcome
    reason: str
    dispatches_agent: bool
    local_landing_succeeds: bool
    reachable_by_integration: bool


@dataclass(frozen=True)
class _FaultOutcome:
    """The typed outcome the simulated environment produced for one fault."""

    kind: FaultKind
    outcome: FaultOutcome
    reason: str
    phase: FaultPhase
    effect: FaultEffect


_ENTRIES: tuple[_FaultClassification, ...] = (
    _FaultClassification(
        kind=FaultKind.NONE,
        row="control",
        phase="any",
        effect="noop",
        outcome="succeeded",
        reason="no fault injected",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.GIT_TIMEOUT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.GIT_TIMEOUT_PARTIAL_OUTPUT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s with partial output",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.GIT_IGNORES_SIGTERM,
        row="A",
        phase="landing",
        effect="fail",
        outcome="killed",
        reason="git merge --ff-only ignored SIGTERM and was force-killed",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.GIT_KILLED_MID_WRITE,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="killed",
        reason="git rebase was killed mid-write and left a stale index.lock",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.LOCK_CONTENTION,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: Unable to create .git/index.lock: File exists",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.STALE_LOCK,
        row="A",
        phase="observation",
        effect="recover",
        outcome="succeeded",
        reason="stale index.lock from a killed Ralph child was recovered; the next observation proceeded",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.LIVE_FOREIGN_LOCK,
        row="A",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: a live foreign process holds the lock on the target ref",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.AUTO_GC_CONCURRENT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: a concurrent auto-gc holds the object store",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.HOOK_PROMPT_HANG,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s waiting for a hook prompt",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.INTERACTIVE_PROMPT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s waiting for an interactive prompt",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.REAL_CONFLICT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase stopped on a content conflict",
        dispatches_agent=True,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.DISK_FULL,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: no space left on device",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.INODE_EXHAUSTION,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: too many open files (inode exhaustion)",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.READ_ONLY_FILESYSTEM,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: read-only file system",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.PERMISSION_DENIED,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: permission denied",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.HOST_OVERLOAD,
        row="B",
        phase="landing",
        effect="fail",
        outcome="timed_out",
        reason="git merge --ff-only timed out after 120.0s: the host is overloaded",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.WALL_CLOCK_JUMP,
        row="B",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the wall clock jumped; deadlines are re-read from the monotonic clock",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.CRASH_BEFORE_STEP,
        row="B",
        phase="any",
        effect="crash_before",
        outcome="killed",
        reason="Ralph was killed before the step completed",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.CRASH_AFTER_STEP,
        row="B",
        phase="any",
        effect="crash_after",
        outcome="killed",
        reason="Ralph was killed after the step completed",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.WORKTREE_DELETED,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the worktree directory was deleted",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_OUTAGE,
        row="C",
        phase="remote",
        effect="fail",
        outcome="unknown",
        reason="the remote is unreachable; the local landing is unaffected",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_DNS_FAILURE,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote fetch failed: DNS resolution failed",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_TLS_FAILURE,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote fetch failed: the TLS handshake failed",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_RATE_LIMITED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="timed_out",
        reason="the remote fetch failed: rate limited (429) with Retry-After",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_PUSH_REJECTED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote push was rejected: non-fast-forward",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_DIVERGED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote target diverged: it was force-pushed",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_FETCH_ONLY,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote is reachable for fetch but refuses pushes",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.TARGET_CHECKED_OUT_ELSEWHERE,
        row="D",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: the target is checked out in another worktree",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.HEAD_DETACHED,
        row="D",
        phase="observation",
        effect="recover",
        outcome="succeeded",
        reason="detached HEAD was reattached to the feature branch",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.CONCURRENT_TARGET_ADVANCE,
        row="D",
        phase="landing",
        effect="concurrent_advance",
        outcome="definitely_failed",
        reason="fast-forward rejected: target advanced concurrently",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.RECORD_UNREADABLE,
        row="D",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the durable integration record is unreadable; Git is ground truth",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.FOREIGN_OPERATION,
        row="D",
        phase="rebase",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: a foreign Git operation is in progress in this repository",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.AGENT_ACTIVE,
        row="E",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: agent is currently active in worktree",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.AGENT_LEFT_LOCK,
        row="E",
        phase="rebase",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: the agent's own Git still holds a lock in this worktree",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.AGENT_NO_CHANGE,
        row="E",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the resolver produced no change; the ladder advances instead of redispatching",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.BACKGROUND_TICKER_OVERLAP,
        row="E",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: a background integration ticker already owns this repository",
        dispatches_agent=False,
        local_landing_succeeds=False,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.STALL_WATCHDOG,
        row="F",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="no integration progress for the bounded window; the run is stalled, not silent",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=True,
    ),
    _FaultClassification(
        kind=FaultKind.SHUTDOWN_STALL,
        row="F",
        phase="shutdown",
        effect="defer",
        outcome="killed",
        reason="shutdown was interrupted; the integration record stays resumable",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=False,
    ),
    _FaultClassification(
        kind=FaultKind.REPORT_VALIDATION_FAILURE,
        row="F",
        phase="shutdown",
        effect="defer",
        outcome="definitely_failed",
        reason="the run-time report failed validation; a degraded partial report was emitted",
        dispatches_agent=False,
        local_landing_succeeds=True,
        reachable_by_integration=False,
    ),
)

_CATALOGUE: MappingProxyType[FaultKind, _FaultClassification] = MappingProxyType(
    {entry.kind: entry for entry in _ENTRIES}
)


def classification_for(kind: FaultKind) -> _FaultClassification:
    """Return the registered classification of ``kind``.

    Args:
        kind: The fault kind to classify.

    Returns:
        The registered classification.

    Raises:
        LookupError: When ``kind`` is not registered. An unregistered
            fault kind cannot be classified into a typed outcome, and the
            catalogue guard fails on it.
    """
    try:
        return _CATALOGUE[kind]
    except KeyError as exc:
        raise LookupError(
            f"fault kind {kind.name} is not classified in the adversity catalogue"
        ) from exc


def catalogue() -> Mapping[FaultKind, _FaultClassification]:
    """Return the whole registry (read-only)."""
    return _CATALOGUE


def registered_outcome(kind: FaultKind) -> _FaultOutcome:
    """Return the typed outcome registered for ``kind``.

    The outcome is built from the registration, so the simulated
    environment and the catalogue tests can never disagree about what a
    fault kind means.

    Raises:
        LookupError: When ``kind`` is not registered.
    """
    classification = classification_for(kind)
    return _FaultOutcome(
        kind=kind,
        outcome=classification.outcome,
        reason=classification.reason,
        phase=classification.phase,
        effect=classification.effect,
    )


def catalogue_rows() -> tuple[str, ...]:
    """Return the catalogue rows that carry at least one named fault kind."""
    return tuple(row for row in CATALOGUE_ROWS if any(entry.row == row for entry in _ENTRIES))
