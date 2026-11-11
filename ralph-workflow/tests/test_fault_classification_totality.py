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

_TYPED_OUTCOMES: frozenset[str] = frozenset(
    {"succeeded", "definitely_failed", "timed_out", "killed", "unknown"}
)


class _NamedKind(Protocol):
    @property
    def name(self) -> str: ...


class _TypedOutcome(Protocol):
    @property
    def outcome(self) -> str: ...

    @property
    def reason(self) -> str: ...


@dataclass(frozen=True)
class _UnclassifiedFaultKind:
    name: str = "DUMMY_UNCLASSIFIED"


def _registered_outcome_or_none(kind: object) -> _TypedOutcome | None:
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
    violations = _unclassified_kinds(kinds, lookup)
    assert not violations, "fault kinds without a typed outcome: " + "; ".join(violations)


_CATALOGUE_ROW_TO_FAULT_KIND: dict[str, tuple[FaultKind, ...]] = {
    "A.1": (FaultKind.GIT_TIMEOUT, FaultKind.GIT_TIMEOUT_PARTIAL_OUTPUT),
    "A.2": (
        FaultKind.GIT_IGNORES_SIGTERM,
        FaultKind.CRASH_BEFORE_STEP,
        FaultKind.CRASH_AFTER_STEP,
    ),
    "A.3": (FaultKind.GIT_KILLED_MID_WRITE,),
    "A.4": (
        FaultKind.LOCK_CONTENTION,
        FaultKind.LIVE_FOREIGN_LOCK,
        FaultKind.STALE_LOCK,
        FaultKind.AGENT_LEFT_LOCK,
    ),
    "A.5": (FaultKind.AUTO_GC_CONCURRENT, FaultKind.HOOK_PROMPT_HANG),
    "A.6": (FaultKind.INTERACTIVE_PROMPT,),
    "B.1": (
        FaultKind.DISK_FULL,
        FaultKind.INODE_EXHAUSTION,
        FaultKind.READ_ONLY_FILESYSTEM,
        FaultKind.PERMISSION_DENIED,
        FaultKind.HOST_OVERLOAD,
    ),
    "B.2": (FaultKind.WALL_CLOCK_JUMP,),
    "B.3": (FaultKind.CRASH_BEFORE_STEP, FaultKind.CRASH_AFTER_STEP),
    "B.4": (FaultKind.WORKTREE_DELETED,),
    "C.1": (
        FaultKind.REMOTE_OUTAGE,
        FaultKind.REMOTE_DNS_FAILURE,
        FaultKind.REMOTE_TLS_FAILURE,
        FaultKind.REMOTE_RATE_LIMITED,
    ),
    "C.2": (FaultKind.REMOTE_PUSH_REJECTED, FaultKind.REMOTE_DIVERGED),
    "C.3": (FaultKind.REMOTE_FETCH_ONLY,),
    "D.1": (FaultKind.TARGET_CHECKED_OUT_ELSEWHERE,),
    "D.2": (
        FaultKind.HEAD_DETACHED,
        FaultKind.FOREIGN_OPERATION,
        FaultKind.CONCURRENT_TARGET_ADVANCE,
    ),
    "D.3": (FaultKind.RECORD_UNREADABLE,),
    "E.1": (
        FaultKind.AGENT_ACTIVE,
        FaultKind.AGENT_LEFT_LOCK,
        FaultKind.HEAD_DETACHED,
        FaultKind.AGENT_NO_CHANGE,
    ),
    "E.2": (FaultKind.BACKGROUND_TICKER_OVERLAP,),
    "F.1": (FaultKind.STALL_WATCHDOG,),
    "F.2": (FaultKind.SHUTDOWN_STALL, FaultKind.REPORT_VALIDATION_FAILURE),
    "F.3": (FaultKind.REPORT_VALIDATION_FAILURE,),
}

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
    _assert_totally_classified(_fake_git_fault.FaultKind, _registered_outcome_or_none)


def test_the_totality_guard_fails_on_an_unclassified_fault_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy = _UnclassifiedFaultKind()
    extended = (*tuple(FaultKind), dummy)
    monkeypatch.setattr(_fake_git_fault, "FaultKind", extended)

    with pytest.raises(AssertionError) as failure:
        _assert_totally_classified(_fake_git_fault.FaultKind, _registered_outcome_or_none)

    assert dummy.name in str(failure.value)


def test_the_registry_names_no_fault_kind_the_enum_does_not_have() -> None:
    members = set(FaultKind)
    stale = sorted(kind.name for kind in catalogue() if kind not in members)
    assert not stale, "catalogue entries without a fault kind: " + ", ".join(stale)


def test_every_catalogue_row_maps_to_a_named_fault_kind() -> None:
    members = set(FaultKind)
    for row, kinds in _CATALOGUE_ROW_TO_FAULT_KIND.items():
        assert kinds, f"catalogue row {row} names no fault kind"
        for kind in kinds:
            assert kind in members, (
                f"catalogue row {row} names {kind.name}, which is not a FaultKind member"
            )


def test_every_catalogue_row_is_accounted_for() -> None:
    assert set(_CATALOGUE_ROW_TO_FAULT_KIND) == set(_CATALOGUE_ROW_UNREPRESENTED)

    sections = {row.split(".", 1)[0] for row in _CATALOGUE_ROW_TO_FAULT_KIND}
    assert sections == set(CATALOGUE_ROWS)

    for row, gaps in _CATALOGUE_ROW_UNREPRESENTED.items():
        for gap in gaps:
            assert gap.strip(), f"catalogue row {row} carries an empty missing-coverage finding"
