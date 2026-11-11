"""The pending commit-failure diagnostic handed from the commit executor to the reducer.

The commit executor reports a failed commit with the ``COMMIT_FAILURE`` event,
which carries no diagnostic of its own, so the diagnostic travels beside the
event in the pending slot this module owns.

The slot is a handoff, not a log. A diagnostic is bound to the approval the
failed attempt was committed against, so the reducer can tell the diagnostic of
the attempt it is routing apart from a diagnostic another attempt left behind,
and the next attempt takes the slot over so a diagnostic never outlives the
attempt that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingCommitFailure:
    """The diagnostic of a failed commit attempt, bound to that attempt's approval.

    ``approved_tree_sha`` is the approved tree SHA the failed attempt was
    committed against (``None`` when the attempt was not SHA-bound). It is the
    only identity a commit attempt carries across the executor/reducer
    boundary, so it is what tells the diagnostic of THIS attempt apart from a
    diagnostic another attempt left behind.
    """

    reason: str
    approved_tree_sha: str | None


class _PendingSlot:
    """The pending commit-failure diagnostic awaiting the reducer's routing."""

    pending: PendingCommitFailure | None = None


_slot = _PendingSlot()


def record_failure(reason: str | None, approved_tree_sha: str | None = None) -> None:
    """Record (or clear, with ``reason=None``) the pending commit-failure diagnostic.

    A diagnostic recorded without an approval binding applies to a state whose
    current approval is also absent.
    """
    _slot.pending = (
        PendingCommitFailure(reason=reason, approved_tree_sha=approved_tree_sha)
        if reason is not None
        else None
    )


def pending_reason() -> str | None:
    """Return the pending diagnostic's reason without consuming it."""
    pending = _slot.pending
    return pending.reason if pending is not None else None


def consume_failure() -> PendingCommitFailure | None:
    """Return the pending diagnostic and clear the pending slot.

    Consuming is what ends the diagnostic's life: a diagnostic that is never
    consumed belongs to an attempt whose failure was never routed, and must
    not reach a later routing decision.
    """
    pending = _slot.pending
    _slot.pending = None
    return pending
