from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_catchup_coordination import _try_lock, _unlock
from ralph.pipeline.lease_outcome_kind import LeaseOutcomeKind
from ralph.pipeline.lease_unknown_reason import LeaseUnknownReason

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class LeaseOutcome:
    kind: LeaseOutcomeKind
    reason: LeaseUnknownReason | None = None
    evidence: str | None = None

    @property
    def acquired(self) -> bool:
        return self.kind is LeaseOutcomeKind.ACQUIRED

    def __bool__(self) -> bool:
        return self.acquired

    def describe(self, busy_message: str) -> str:
        if self.kind is LeaseOutcomeKind.HELD_BY_LIVE_OWNER:
            return busy_message
        reason = self.reason.name if self.reason is not None else "UNCLASSIFIED"
        detail = f"; {self.evidence}" if self.evidence else ""
        return (
            f"integration lease state unknown ({reason}{detail}); "
            "backing off and retrying without dispatching"
        )

    @classmethod
    def acquired_outcome(cls) -> LeaseOutcome:
        return cls(kind=LeaseOutcomeKind.ACQUIRED)

    @classmethod
    def held_by_live_owner(cls) -> LeaseOutcome:
        return cls(kind=LeaseOutcomeKind.HELD_BY_LIVE_OWNER)

    @classmethod
    def unknown(cls, reason: LeaseUnknownReason, evidence: str | None = None) -> LeaseOutcome:
        return cls(kind=LeaseOutcomeKind.UNKNOWN, reason=reason, evidence=evidence)


@contextmanager
def integration_transaction(root: Path) -> Iterator[LeaseOutcome]:
    git_marker = root / ".git"
    if not git_marker.exists():
        yield LeaseOutcome.acquired_outcome()
        return
    if git_marker.is_dir() and not (git_marker / "HEAD").exists():
        yield LeaseOutcome.unknown(
            LeaseUnknownReason.GIT_DIR_INVALID,
            evidence=str(git_marker),
        )
        return
    observed = run_git(("rev-parse", "--absolute-git-dir"), cwd=root, label="integration:lock-dir")
    if observed.returncode != 0 or not observed.stdout.strip():
        yield LeaseOutcome.unknown(
            LeaseUnknownReason.GIT_DIR_UNRESOLVED,
            evidence=(observed.stderr or "").strip() or f"rc={observed.returncode}",
        )
        return
    path = Path(observed.stdout.strip()) / "ralph-auto-integrate.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
    except OSError as error:
        yield LeaseOutcome.unknown(LeaseUnknownReason.LOCK_IO_ERROR, evidence=str(error.errno))
        return
    with handle:
        acquired = _try_lock(handle)
        outcome = LeaseOutcome.acquired_outcome() if acquired else LeaseOutcome.held_by_live_owner()
        try:
            yield outcome
        finally:
            if outcome.kind is LeaseOutcomeKind.ACQUIRED:
                _unlock(handle)


__all__ = [
    "LeaseOutcome",
    "integration_transaction",
]
