"""Totality guard for the typed outcome classification of every fault kind.

Acceptance Criterion 9 of the integration adversity request states:

    "A new fault kind cannot be added without being classified into a typed
    outcome (a missing case fails a test)."

This file makes an UNCLASSIFIED fault kind unrepresentable in practice:

* :func:`test_every_fault_kind_has_a_typed_outcome_classification` iterates
  EVERY member of :class:`~tests._fake_git_fault.FaultKind` and fails when a
  member has no registered classification, when its outcome falls outside the
  closed typed vocabulary, or when it states no cause.
* :func:`test_the_totality_guard_fails_on_an_unclassified_fault_kind` proves
  that guard is real: it injects a fault kind nobody classified and shows the
  guard names it.
* :func:`test_the_registry_names_no_fault_kind_the_enum_does_not_have` keeps
  the registry and the enum in one-to-one correspondence, so the registry
  cannot carry a classification for a kind that does not exist.
* :func:`test_every_catalogue_row_maps_to_a_named_fault_kind` maps every row
  of the failure-mode catalogue (sections A-F) to a named fault kind, and
  :func:`test_every_catalogue_row_is_accounted_for` keeps the row inventory
  complete.

The sibling file ``tests/test_integration_fault_catalogue.py`` asserts that
the simulated environment CONSUMES every named fault; this file asserts that
every named fault is CLASSIFIED, so the two together close the loop.

The catalogue rows and the missing-coverage findings are embedded here as
literals. The request document lives outside the maintained package, and a
test must not read files at run time (see
``ralph/testing/audit_test_policy.py``), so the inventory below is the
canonical, reviewable copy of the catalogue this guard enforces.

Two enum members are deliberately absent from the row table:
``FaultKind.NONE`` is the no-fault baseline, not a failure mode, and
``FaultKind.REAL_CONFLICT`` is the content conflict the catalogue's
"unless it is a real content conflict" clause names -- the one fault that may
dispatch a resolver agent, not a failure of the environment. The coverage
check runs in one direction only (every catalogue ROW names a fault kind),
because the enum is owned by another unit and a row table that must be
updated whenever the enum grows would couple the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pytest

from tests import _fake_git_fault
from tests._fake_git_fault import (
    CATALOGUE_ROWS,
    FaultKind,
    catalogue,
    registered_outcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

pytestmark = [pytest.mark.timeout_seconds(10)]

#: The closed typed-outcome vocabulary of the product request ("Cross-cutting
#: requirements the catalogue implies"): every external call returns one of
#: these, and a caller matches on the type, never on message text.
_TYPED_OUTCOMES: frozenset[str] = frozenset(
    {"succeeded", "definitely_failed", "timed_out", "killed", "unknown"}
)


class _NamedKind(Protocol):
    """The shape of a fault kind the totality guard reads."""

    @property
    def name(self) -> str:
        """The member's name; the failure message names the offending kind."""
        ...


class _TypedOutcome(Protocol):
    """The shape of a registered outcome the totality guard reads."""

    @property
    def outcome(self) -> str:
        """The typed outcome of the external call."""
        ...

    @property
    def reason(self) -> str:
        """The user-facing reason, i.e. the cause of a definite failure."""
        ...


@dataclass(frozen=True)
class _UnclassifiedFaultKind:
    """A fault kind that exists in the enum but was never classified.

    The negative test injects it into the enum iteration to prove the
    totality guard fails on a member nobody classified. It is a test double:
    it is never registered, never consumed and never shipped.
    """

    name: str = "DUMMY_UNCLASSIFIED"


def _registered_outcome_or_none(kind: object) -> _TypedOutcome | None:
    """Return the registered outcome of ``kind``, or ``None`` when it has none.

    A member the enum does not know cannot have a registration, and a member
    the registry does not know cannot be classified into a typed outcome.
    Both are reported as "no typed outcome" by the guard.

    Args:
        kind: The fault kind to look up.

    Returns:
        The registered outcome, or ``None`` when the kind is unregistered.
    """
    if not isinstance(kind, FaultKind):
        return None
    try:
        return registered_outcome(kind)
    except LookupError:
        return None


def _unclassified_kinds[K: _NamedKind](
    kinds: Iterable[K],
    lookup: Callable[[K], _TypedOutcome | None],
) -> tuple[str, ...]:
    """Return one message per fault kind that lacks a typed outcome.

    Args:
        kinds: The fault kinds to check.
        lookup: Returns the registered outcome of a kind, or ``None`` when
            the kind has no registration.

    Returns:
        One message per offending kind, naming the kind.
    """
    violations: list[str] = []
    for kind in kinds:
        outcome = lookup(kind)
        if outcome is None:
            violations.append(
                f"{kind.name}: no registered classification, so it has no typed outcome"
            )
            continue
        if outcome.outcome not in _TYPED_OUTCOMES:
            violations.append(
                f"{kind.name}: outcome {outcome.outcome!r} is outside the typed vocabulary "
                f"{sorted(_TYPED_OUTCOMES)}"
            )
        if not outcome.reason.strip():
            violations.append(f"{kind.name}: no user-facing cause was stated")
    return tuple(violations)


def _assert_totally_classified[K: _NamedKind](
    kinds: Iterable[K],
    lookup: Callable[[K], _TypedOutcome | None],
) -> None:
    """Fail when any of ``kinds`` lacks a typed outcome classification.

    Args:
        kinds: The fault kinds to check.
        lookup: Returns the registered outcome of a kind, or ``None`` when
            the kind has no registration.

    Raises:
        AssertionError: When a kind is not classified. The message names
            every offending kind.
    """
    violations = _unclassified_kinds(kinds, lookup)
    assert not violations, "fault kinds without a typed outcome: " + "; ".join(violations)


#: Every row of the failure-mode catalogue of the product request
#: (``.agent/PRODUCT_CRITERIA.md``, "Failure-mode catalogue", sections A-F)
#: and the named fault kinds that represent it. The key names the row; the
#: comment above the key quotes the catalogue text the row comes from.
_CATALOGUE_ROW_TO_FAULT_KIND: dict[str, tuple[FaultKind, ...]] = {
    # -- Section A: "A Git command misbehaves" ------------------------------
    # A.1 "Times out with no output; times out with partial output."
    "A.1": (FaultKind.GIT_TIMEOUT, FaultKind.GIT_TIMEOUT_PARTIAL_OUTPUT),
    # A.2 "Ignores SIGTERM and SIGKILL (uninterruptible I/O, hung network
    # filesystem); the supervisor itself dies; orphaned grandchildren ...
    # keep running; PID reuse after restart."
    "A.2": (
        FaultKind.GIT_IGNORES_SIGTERM,
        FaultKind.CRASH_BEFORE_STEP,
        FaultKind.CRASH_AFTER_STEP,
    ),
    # A.3 "Killed mid-write: stale index.lock, HEAD.lock, packed-refs.lock,
    # refs/**.lock, shallow.lock; half-written rebase-merge/, MERGE_HEAD,
    # ORIG_HEAD; truncated or corrupt index; empty or corrupt loose object."
    "A.3": (FaultKind.GIT_KILLED_MID_WRITE,),
    # A.4 "A lock held by a live foreign process (another worktree, an IDE,
    # the agent's own Git) versus a stale one: distinguish by evidence,
    # never by age alone, and never delete a live holder's lock."
    "A.4": (
        FaultKind.LOCK_CONTENTION,
        FaultKind.LIVE_FOREIGN_LOCK,
        FaultKind.STALE_LOCK,
        FaultKind.AGENT_LEFT_LOCK,
    ),
    # A.5 "Auto-gc or repack running concurrently; fsmonitor hung; hooks
    # (pre-commit, pre-push, post-merge, post-checkout) hang, fail or prompt."
    "A.5": (FaultKind.AUTO_GC_CONCURRENT, FaultKind.HOOK_PROMPT_HANG),
    # A.6 "Interactive prompt waits on a TTY (credentials, ssh host key,
    # editor, GPG pinentry, `git rebase` todo editor)."
    "A.6": (FaultKind.INTERACTIVE_PROMPT,),
    # -- Section B: "Machine and filesystem" --------------------------------
    # B.1 "Disk full (ENOSPC), inode exhaustion, read-only filesystem,
    # permission denied, quota exceeded, slow disk, host overload or swap
    # storm."
    "B.1": (
        FaultKind.DISK_FULL,
        FaultKind.INODE_EXHAUSTION,
        FaultKind.READ_ONLY_FILESYSTEM,
        FaultKind.PERMISSION_DENIED,
        FaultKind.HOST_OVERLOAD,
    ),
    # B.2 "Wall-clock jumps forward or back, system suspend/resume, so
    # deadlines and back-off must use a monotonic clock and survive a resume
    # without mass timeouts or an instant budget burn."
    "B.2": (FaultKind.WALL_CLOCK_JUMP,),
    # B.3 "Ralph itself OOM-killed, SIGKILLed or power-lost at any step
    # (covered by the crash-before/after-every-step model); SIGINT or SIGTERM
    # arriving inside a critical section."
    "B.3": (FaultKind.CRASH_BEFORE_STEP, FaultKind.CRASH_AFTER_STEP),
    # B.4 "Worktree directory deleted, moved or prunable; `.git` file
    # pointing to a missing gitdir; sparse checkout, submodules,
    # case-insensitive filesystem."
    "B.4": (FaultKind.WORKTREE_DELETED,),
    # -- Section C: "Remote and network" ------------------------------------
    # C.1 "DNS failure, TLS failure, connection reset, expired credentials,
    # 401/403, rate limiting (429 with Retry-After), slow but alive fetch,
    # partial pack, hung push, LFS failure."
    "C.1": (
        FaultKind.REMOTE_OUTAGE,
        FaultKind.REMOTE_DNS_FAILURE,
        FaultKind.REMOTE_TLS_FAILURE,
        FaultKind.REMOTE_RATE_LIMITED,
    ),
    # C.2 "Push rejected: non-fast-forward, protected branch, server-side
    # hook, size limit. Remote force-pushed (history rewritten), remote
    # branch deleted, remote renamed or repointed, remote repository
    # replaced."
    "C.2": (FaultKind.REMOTE_PUSH_REJECTED, FaultKind.REMOTE_DIVERGED),
    # C.3 "Remote reachable for fetch but not push (and the reverse)."
    "C.3": (FaultKind.REMOTE_FETCH_ONLY,),
    # -- Section D: "Repository topology" -----------------------------------
    # D.1 "`<target>` checked out in another worktree (a fast-forward there
    # would mutate it, or is refused), possibly dirty or mid-operation."
    "D.1": (FaultKind.TARGET_CHECKED_OUT_ELSEWHERE,),
    # D.2 "Branch unborn, deleted, or `HEAD` pointing at a missing ref;
    # feature branch renamed; two Ralph processes or sessions on one
    # repository; a concurrent landing from another worktree between observe
    # and act."
    "D.2": (
        FaultKind.HEAD_DETACHED,
        FaultKind.FOREIGN_OPERATION,
        FaultKind.CONCURRENT_TARGET_ADVANCE,
    ),
    # D.3 "Ledger or record unreadable, corrupt, locked by a dead process,
    # written by an older or newer version, or on a different filesystem than
    # the worktree."
    "D.3": (FaultKind.RECORD_UNREADABLE,),
    # -- Section E: "Agents and background work" ----------------------------
    # E.1 "Agent runs for hours with no progress; agent runs Git itself and
    # leaves locks, an operation, or a moved `HEAD`; agent is killed
    # mid-edit; agent produces no change; agent output touches paths outside
    # the conflict."
    "E.1": (
        FaultKind.AGENT_ACTIVE,
        FaultKind.AGENT_LEFT_LOCK,
        FaultKind.HEAD_DETACHED,
        FaultKind.AGENT_NO_CHANGE,
    ),
    # E.2 "Background tickers (catch-up, remote reconcile, backup-ref
    # writer, health probe) overlapping each other or a foreground phase; a
    # ticker that fails forever on the same error."
    "E.2": (FaultKind.BACKGROUND_TICKER_OVERLAP,),
    # -- Section F: "Observability and shutdown" ----------------------------
    # F.1 "A stall watchdog: no integration progress for a bounded time
    # becomes a visible, accurate state with its cause from the current
    # observation (not one WARNING line), and never a silent multi-hour
    # wait."
    "F.1": (FaultKind.STALL_WATCHDOG,),
    # F.2 "Shutdown is total: interrupt or crash at any point still emits a
    # valid run-time report, releases locks Ralph owns, leaves the record
    # resumable, and reports pending integration accurately. A
    # report-validation failure is itself degraded gracefully (partial
    # report plus the reason), never lost."
    "F.2": (FaultKind.SHUTDOWN_STALL, FaultKind.REPORT_VALIDATION_FAILURE),
    # F.3 "Metrics stay truthful when no agent ran (zero counts are valid,
    # not a validation error)."
    "F.3": (FaultKind.REPORT_VALIDATION_FAILURE,),
}

#: The sub-items of a catalogue row that the current ``FaultKind`` enum
#: cannot name. Each entry is a MISSING COVERAGE finding: the simulated
#: environment cannot inject that failure mode, so it has no bounded
#: expectation and no user-facing reason. The owning unit extends the enum
#: and the row then moves into ``_CATALOGUE_ROW_TO_FAULT_KIND``.
_CATALOGUE_ROW_UNREPRESENTED: dict[str, tuple[str, ...]] = {
    "A.1": (),
    "A.2": (
        "orphaned grandchildren (hooks, ssh, credential helpers, fsmonitor daemon) keep running",
        "PID reuse after restart",
    ),
    "A.3": (
        "HEAD.lock, packed-refs.lock, refs/**.lock and shallow.lock left behind",
        "half-written rebase-merge/, MERGE_HEAD and ORIG_HEAD",
        "truncated or corrupt index",
        "empty or corrupt loose object",
    ),
    "A.4": (),
    "A.5": (
        "repack running concurrently",
        "fsmonitor hung",
    ),
    "A.6": (),
    "B.1": (
        "quota exceeded",
        "slow disk",
    ),
    "B.2": ("system suspend/resume",),
    "B.3": ("SIGINT or SIGTERM arriving inside a critical section",),
    "B.4": (
        "worktree moved or prunable",
        "`.git` file pointing to a missing gitdir",
        "sparse checkout",
        "submodules",
        "case-insensitive filesystem",
    ),
    "C.1": (
        "connection reset",
        "expired credentials",
        "401/403",
        "slow but alive fetch",
        "partial pack",
        "hung push",
        "LFS failure",
    ),
    "C.2": (
        "push rejected: protected branch",
        "push rejected: server-side hook",
        "push rejected: size limit",
        "remote branch deleted",
        "remote renamed or repointed",
        "remote repository replaced",
    ),
    "C.3": ("remote reachable for push but not for fetch",),
    "D.1": (
        "the other worktree is dirty",
        "the other worktree is mid-operation",
    ),
    "D.2": (
        "branch unborn",
        "branch deleted",
        "`HEAD` pointing at a ref that does not exist",
        "feature branch renamed",
    ),
    "D.3": (
        "record corrupt",
        "record locked by a dead process",
        "record written by an older or newer version",
        "record on a different filesystem than the worktree",
    ),
    "E.1": (
        "agent is killed mid-edit",
        "agent output touches paths outside the conflict",
    ),
    "E.2": ("a ticker that fails forever on the same error",),
    "F.1": (),
    "F.2": (),
    "F.3": (),
}


def test_every_fault_kind_has_a_typed_outcome_classification() -> None:
    """AC-9: no fault kind exists without a typed outcome classification.

    The guard iterates EVERY member of the enum and fails on a member that
    has no registered classification, on an outcome outside the closed
    vocabulary, or on a failure that states no cause.
    """
    _assert_totally_classified(_fake_git_fault.FaultKind, _registered_outcome_or_none)


def test_the_totality_guard_fails_on_an_unclassified_fault_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is real: a member nobody classified makes it fail, by name.

    The dummy kind is injected into the enum iteration only; it is never
    registered, so the guard has to report it.
    """
    dummy = _UnclassifiedFaultKind()
    extended = (*tuple(FaultKind), dummy)
    monkeypatch.setattr(_fake_git_fault, "FaultKind", extended)

    with pytest.raises(AssertionError) as failure:
        _assert_totally_classified(_fake_git_fault.FaultKind, _registered_outcome_or_none)

    assert dummy.name in str(failure.value)


def test_the_registry_names_no_fault_kind_the_enum_does_not_have() -> None:
    """The registry and the enum stay in one-to-one correspondence."""
    members = set(FaultKind)
    stale = sorted(kind.name for kind in catalogue() if kind not in members)
    assert not stale, "catalogue entries without a fault kind: " + ", ".join(stale)


def test_every_catalogue_row_maps_to_a_named_fault_kind() -> None:
    """AC-9: every catalogue row (A-F) is a named fault kind."""
    members = set(FaultKind)
    for row, kinds in _CATALOGUE_ROW_TO_FAULT_KIND.items():
        assert kinds, f"catalogue row {row} names no fault kind"
        for kind in kinds:
            assert kind in members, (
                f"catalogue row {row} names {kind.name}, which is not a FaultKind member"
            )


def test_every_catalogue_row_is_accounted_for() -> None:
    """Every catalogue row is either represented or an explicit gap.

    A row that is silently dropped from both tables would hide a catalogue
    failure mode, so the row inventory of the two tables must cover every
    section of the catalogue and must agree on the row set.
    """
    assert set(_CATALOGUE_ROW_TO_FAULT_KIND) == set(_CATALOGUE_ROW_UNREPRESENTED)

    sections = {row.split(".", 1)[0] for row in _CATALOGUE_ROW_TO_FAULT_KIND}
    assert sections == set(CATALOGUE_ROWS)

    for row, gaps in _CATALOGUE_ROW_UNREPRESENTED.items():
        for gap in gaps:
            assert gap.strip(), f"catalogue row {row} carries an empty missing-coverage finding"
