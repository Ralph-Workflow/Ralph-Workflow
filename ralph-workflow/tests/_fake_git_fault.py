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

The catalogue SUB-ROWS that had no named fault kind until now are
mapped to their member in ``tests._fake_git_env.SIMULATED_CATALOGUE_ROWS``,
so the coverage of the catalogue is reviewable in one place.
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
    CORRUPT_INDEX = auto()
    CORRUPT_LOOSE_OBJECT = auto()
    HALF_WRITTEN_OPERATION_STATE = auto()
    LOCK_CONTENTION = auto()
    STALE_LOCK = auto()
    REF_LOCK_LEFT_BEHIND = auto()
    LIVE_FOREIGN_LOCK = auto()
    AUTO_GC_CONCURRENT = auto()
    FSMONITOR_HUNG = auto()
    HOOK_PROMPT_HANG = auto()
    INTERACTIVE_PROMPT = auto()
    REAL_CONFLICT = auto()

    # -- Row B: machine and filesystem ----------------------------------
    DISK_FULL = auto()
    INODE_EXHAUSTION = auto()
    QUOTA_EXCEEDED = auto()
    READ_ONLY_FILESYSTEM = auto()
    PERMISSION_DENIED = auto()
    SLOW_DISK = auto()
    HOST_OVERLOAD = auto()
    WALL_CLOCK_JUMP = auto()
    SYSTEM_SUSPEND_RESUME = auto()
    CRASH_BEFORE_STEP = auto()
    CRASH_AFTER_STEP = auto()
    SIGNAL_IN_CRITICAL_SECTION = auto()
    WORKTREE_DELETED = auto()
    WORKTREE_MOVED_OR_PRUNABLE = auto()
    WORKTREE_MISSING_GITDIR = auto()
    SPARSE_CHECKOUT = auto()
    SUBMODULES_PRESENT = auto()
    CASE_INSENSITIVE_FILESYSTEM = auto()

    # -- Row C: remote and network --------------------------------------
    REMOTE_OUTAGE = auto()
    REMOTE_DNS_FAILURE = auto()
    REMOTE_TLS_FAILURE = auto()
    REMOTE_CONNECTION_RESET = auto()
    REMOTE_FORBIDDEN = auto()
    REMOTE_EXPIRED_CREDENTIALS = auto()
    REMOTE_RATE_LIMITED = auto()
    REMOTE_SLOW_FETCH = auto()
    REMOTE_PUSH_REJECTED = auto()
    REMOTE_PUSH_PROTECTED_BRANCH = auto()
    REMOTE_PUSH_HOOK_REJECTED = auto()
    REMOTE_PUSH_SIZE_LIMIT = auto()
    REMOTE_DIVERGED = auto()
    REMOTE_BRANCH_DELETED = auto()
    REMOTE_REPOINTED = auto()
    REMOTE_REPLACED = auto()
    REMOTE_FETCH_ONLY = auto()
    REMOTE_PUSH_ONLY = auto()
    REMOTE_HUNG_PUSH = auto()
    REMOTE_PARTIAL_PACK = auto()
    REMOTE_LFS_FAILURE = auto()

    # -- Row D: repository topology -------------------------------------
    TARGET_CHECKED_OUT_ELSEWHERE = auto()
    OTHER_WORKTREE_DIRTY = auto()
    OTHER_WORKTREE_MID_OPERATION = auto()
    HEAD_DETACHED = auto()
    HEAD_REF_MISSING = auto()
    CONCURRENT_TARGET_ADVANCE = auto()
    BRANCH_UNBORN = auto()
    BRANCH_DELETED = auto()
    FEATURE_BRANCH_RENAMED = auto()
    RECORD_UNREADABLE = auto()
    RECORD_CORRUPT = auto()
    RECORD_OLDER_VERSION = auto()
    RECORD_LOCKED_BY_DEAD_PROCESS = auto()
    RECORD_OTHER_FILESYSTEM = auto()
    FOREIGN_OPERATION = auto()

    # -- Row E: agents and background work ------------------------------
    AGENT_ACTIVE = auto()
    AGENT_LEFT_LOCK = auto()
    AGENT_NO_CHANGE = auto()
    AGENT_KILLED_MID_EDIT = auto()
    AGENT_OUTSIDE_CONFLICT_PATHS = auto()
    BACKGROUND_TICKER_OVERLAP = auto()
    TICKER_FOREVER_FAILING = auto()

    # -- Row F: observability and shutdown ------------------------------
    STALL_WATCHDOG = auto()
    SHUTDOWN_STALL = auto()
    REPORT_VALIDATION_FAILURE = auto()

    # -- Control: the no-fault baseline ---------------------------------
    NONE = auto()


@dataclass(frozen=True)
class _FaultClassification:
    """How one fault kind behaves inside the simulated environment.

    The three flags default to the SAFE value, so a registration states
    only what differs from it: no fault dispatches an agent unless it
    says so, a fault keeps the local landing unless it says otherwise,
    and every fault is reachable by the integration unless it says
    otherwise. The defaults are the policy of the catalogue, so a new
    member cannot be registered by accident with the wrong flags.
    """

    kind: FaultKind
    row: str
    phase: FaultPhase
    effect: FaultEffect
    outcome: FaultOutcome
    reason: str
    dispatches_agent: bool = False
    local_landing_succeeds: bool = True
    reachable_by_integration: bool = True


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
    ),
    _FaultClassification(
        kind=FaultKind.GIT_TIMEOUT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.GIT_TIMEOUT_PARTIAL_OUTPUT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s with partial output",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.GIT_IGNORES_SIGTERM,
        row="A",
        phase="landing",
        effect="fail",
        outcome="killed",
        reason="git merge --ff-only ignored SIGTERM and was force-killed",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.GIT_KILLED_MID_WRITE,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="killed",
        reason="git rebase was killed mid-write and left a stale index.lock",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.CORRUPT_INDEX,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the index is truncated or corrupt",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.LOCK_CONTENTION,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: Unable to create .git/index.lock: File exists",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.STALE_LOCK,
        row="A",
        phase="observation",
        effect="recover",
        outcome="succeeded",
        reason="stale index.lock from a killed Ralph child was recovered; the next observation proceeded",
    ),
    _FaultClassification(
        kind=FaultKind.REF_LOCK_LEFT_BEHIND,
        row="A",
        phase="observation",
        effect="recover",
        outcome="succeeded",
        reason="stale packed-refs.lock from a killed Ralph child was recovered; "
        "the next observation proceeded",
    ),
    _FaultClassification(
        kind=FaultKind.LIVE_FOREIGN_LOCK,
        row="A",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: a live foreign process holds the lock on the target ref",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.AUTO_GC_CONCURRENT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: a concurrent auto-gc holds the object store",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.HOOK_PROMPT_HANG,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s waiting for a hook prompt",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.INTERACTIVE_PROMPT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s waiting for an interactive prompt",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.REAL_CONFLICT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase stopped on a content conflict",
        dispatches_agent=True,
    ),
    _FaultClassification(
        kind=FaultKind.DISK_FULL,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: no space left on device",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.INODE_EXHAUSTION,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: too many open files (inode exhaustion)",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.READ_ONLY_FILESYSTEM,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: read-only file system",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.PERMISSION_DENIED,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: permission denied",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.HOST_OVERLOAD,
        row="B",
        phase="landing",
        effect="fail",
        outcome="timed_out",
        reason="git merge --ff-only timed out after 120.0s: the host is overloaded",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.WALL_CLOCK_JUMP,
        row="B",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the wall clock jumped; deadlines are re-read from the monotonic clock",
    ),
    _FaultClassification(
        kind=FaultKind.CRASH_BEFORE_STEP,
        row="B",
        phase="any",
        effect="crash_before",
        outcome="killed",
        reason="Ralph was killed before the step completed",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.CRASH_AFTER_STEP,
        row="B",
        phase="any",
        effect="crash_after",
        outcome="killed",
        reason="Ralph was killed after the step completed",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.WORKTREE_DELETED,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the worktree directory was deleted",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.WORKTREE_MISSING_GITDIR,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the .git file points at a missing git directory",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_OUTAGE,
        row="C",
        phase="remote",
        effect="fail",
        outcome="unknown",
        reason="the remote is unreachable; the local landing is unaffected",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_DNS_FAILURE,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote fetch failed: DNS resolution failed",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_TLS_FAILURE,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote fetch failed: the TLS handshake failed",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_RATE_LIMITED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="timed_out",
        reason="the remote fetch failed: rate limited (429) with Retry-After",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_PUSH_REJECTED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote push was rejected: non-fast-forward",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_DIVERGED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote target diverged: it was force-pushed",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_FETCH_ONLY,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote is reachable for fetch but refuses pushes",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_HUNG_PUSH,
        row="C",
        phase="remote",
        effect="fail",
        outcome="timed_out",
        reason="the remote push hung and was abandoned; the local landing is unaffected",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_PARTIAL_PACK,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote fetch failed: the pack file is partial",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_EXPIRED_CREDENTIALS,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote fetch failed: the credentials expired",
    ),
    _FaultClassification(
        kind=FaultKind.TARGET_CHECKED_OUT_ELSEWHERE,
        row="D",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: the target is checked out in another worktree",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.HEAD_DETACHED,
        row="D",
        phase="observation",
        effect="recover",
        outcome="succeeded",
        reason="detached HEAD was reattached to the feature branch",
    ),
    _FaultClassification(
        kind=FaultKind.CONCURRENT_TARGET_ADVANCE,
        row="D",
        phase="landing",
        effect="concurrent_advance",
        outcome="definitely_failed",
        reason="fast-forward rejected: target advanced concurrently",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.RECORD_UNREADABLE,
        row="D",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the durable integration record is unreadable; Git is ground truth",
    ),
    _FaultClassification(
        kind=FaultKind.RECORD_CORRUPT,
        row="D",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the durable integration record is corrupt; Git is ground truth",
    ),
    _FaultClassification(
        kind=FaultKind.RECORD_OLDER_VERSION,
        row="D",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the durable integration record was written by an older version; "
        "Git is ground truth",
    ),
    _FaultClassification(
        kind=FaultKind.FOREIGN_OPERATION,
        row="D",
        phase="rebase",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: a foreign Git operation is in progress in this repository",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.AGENT_ACTIVE,
        row="E",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: agent is currently active in worktree",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.AGENT_LEFT_LOCK,
        row="E",
        phase="rebase",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: the agent's own Git still holds a lock in this worktree",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.AGENT_NO_CHANGE,
        row="E",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the resolver produced no change; the ladder advances instead of redispatching",
    ),
    _FaultClassification(
        kind=FaultKind.AGENT_KILLED_MID_EDIT,
        row="E",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the resolver agent was killed mid-edit; the work is preserved and "
        "the state is re-observed",
    ),
    _FaultClassification(
        kind=FaultKind.BACKGROUND_TICKER_OVERLAP,
        row="E",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: a background integration ticker already owns this repository",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.TICKER_FOREVER_FAILING,
        row="E",
        phase="landing",
        effect="fail",
        outcome="unknown",
        reason="the background integration ticker failed on the same error again; "
        "the ladder advances instead of retrying forever",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.STALL_WATCHDOG,
        row="F",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="no integration progress for the bounded window; the run is stalled, not silent",
    ),
    _FaultClassification(
        kind=FaultKind.SHUTDOWN_STALL,
        row="F",
        phase="shutdown",
        effect="defer",
        outcome="killed",
        reason="shutdown was interrupted; the integration record stays resumable",
        reachable_by_integration=False,
    ),
    _FaultClassification(
        kind=FaultKind.REPORT_VALIDATION_FAILURE,
        row="F",
        phase="shutdown",
        effect="defer",
        outcome="definitely_failed",
        reason="the run-time report failed validation; a degraded partial report was emitted",
        reachable_by_integration=False,
    ),
    # -- Row A: the sub-rows that had no named fault kind yet -------------
    _FaultClassification(
        kind=FaultKind.CORRUPT_LOOSE_OBJECT,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: a loose object is empty or corrupt",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.HALF_WRITTEN_OPERATION_STATE,
        row="A",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the in-progress operation state is half-written; the operation kind is unknown",
    ),
    _FaultClassification(
        kind=FaultKind.FSMONITOR_HUNG,
        row="A",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s: the fsmonitor daemon is hung",
        local_landing_succeeds=False,
    ),
    # -- Row B: the sub-rows that had no named fault kind yet -------------
    _FaultClassification(
        kind=FaultKind.QUOTA_EXCEEDED,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the filesystem quota is exceeded",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.SLOW_DISK,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="timed_out",
        reason="git rebase timed out after 120.0s: the disk is slow",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.SYSTEM_SUSPEND_RESUME,
        row="B",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the host was suspended and resumed; the elapsed time is not trustworthy",
    ),
    _FaultClassification(
        kind=FaultKind.SIGNAL_IN_CRITICAL_SECTION,
        row="B",
        phase="any",
        effect="crash_before",
        outcome="killed",
        reason="a SIGINT arrived inside a critical section; the step was interrupted "
        "before it completed",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.WORKTREE_MOVED_OR_PRUNABLE,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the worktree was moved or pruned",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.SPARSE_CHECKOUT,
        row="B",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the working tree is a sparse checkout; the paths are not materialised",
    ),
    _FaultClassification(
        kind=FaultKind.SUBMODULES_PRESENT,
        row="B",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="a nested checkout could not be read; the observation is unknown",
    ),
    _FaultClassification(
        kind=FaultKind.CASE_INSENSITIVE_FILESYSTEM,
        row="B",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the case-insensitive filesystem cannot represent both paths",
        local_landing_succeeds=False,
    ),
    # -- Row C: the sub-rows that had no named fault kind yet -------------
    _FaultClassification(
        kind=FaultKind.REMOTE_CONNECTION_RESET,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote fetch failed: the connection was reset",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_FORBIDDEN,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote fetch failed: the server refused the request (401/403)",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_SLOW_FETCH,
        row="C",
        phase="remote",
        effect="fail",
        outcome="timed_out",
        reason="the remote fetch is slow but alive; it did not finish in time",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_LFS_FAILURE,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote LFS object transfer failed",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_PUSH_PROTECTED_BRANCH,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote push was rejected: the branch is protected",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_PUSH_HOOK_REJECTED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote push was rejected by a server-side hook",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_PUSH_SIZE_LIMIT,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote push was rejected: the push exceeds the size limit",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_BRANCH_DELETED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote target branch was deleted",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_REPOINTED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote target was renamed or repointed",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_REPLACED,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote repository was replaced",
    ),
    _FaultClassification(
        kind=FaultKind.REMOTE_PUSH_ONLY,
        row="C",
        phase="remote",
        effect="fail",
        outcome="definitely_failed",
        reason="the remote is reachable for push but refuses fetches",
    ),
    # -- Row D: the sub-rows that had no named fault kind yet -------------
    _FaultClassification(
        kind=FaultKind.OTHER_WORKTREE_DIRTY,
        row="D",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: the target is checked out in another worktree and that worktree is dirty",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.OTHER_WORKTREE_MID_OPERATION,
        row="D",
        phase="landing",
        effect="refuse",
        outcome="definitely_failed",
        reason="refused: the target is checked out in another worktree and that worktree "
        "is mid-operation",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.HEAD_REF_MISSING,
        row="D",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: HEAD points at a ref that does not exist",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.BRANCH_UNBORN,
        row="D",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the target branch is unborn",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.BRANCH_DELETED,
        row="D",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the target branch was deleted",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.FEATURE_BRANCH_RENAMED,
        row="D",
        phase="rebase",
        effect="fail",
        outcome="definitely_failed",
        reason="git rebase failed: the feature branch was renamed under the run",
        local_landing_succeeds=False,
    ),
    _FaultClassification(
        kind=FaultKind.RECORD_LOCKED_BY_DEAD_PROCESS,
        row="D",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the durable integration record is locked by a dead process; Git is ground truth",
    ),
    _FaultClassification(
        kind=FaultKind.RECORD_OTHER_FILESYSTEM,
        row="D",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the durable integration record lives on another filesystem; Git is ground truth",
    ),
    # -- Row E: the sub-rows that had no named fault kind yet -------------
    _FaultClassification(
        kind=FaultKind.AGENT_OUTSIDE_CONFLICT_PATHS,
        row="E",
        phase="observation",
        effect="defer",
        outcome="unknown",
        reason="the resolver edited paths outside the conflict; the work is preserved and "
        "the state is re-observed",
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
