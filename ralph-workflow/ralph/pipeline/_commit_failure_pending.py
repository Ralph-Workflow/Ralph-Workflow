from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingCommitFailure:
    reason: str
    approved_tree_sha: str | None


class _PendingSlot:
    pending: PendingCommitFailure | None = None


_slot = _PendingSlot()


def record_failure(reason: str | None, approved_tree_sha: str | None = None) -> None:
    _slot.pending = (
        PendingCommitFailure(reason=reason, approved_tree_sha=approved_tree_sha)
        if reason is not None
        else None
    )


def pending_reason() -> str | None:
    pending = _slot.pending
    return pending.reason if pending is not None else None


def consume_failure() -> PendingCommitFailure | None:
    pending = _slot.pending
    _slot.pending = None
    return pending
