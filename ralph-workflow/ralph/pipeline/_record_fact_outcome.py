"""Typed fail-closed verdicts for durable record facts.

This private module holds the closed :class:`RecordFactOutcome` vocabulary
that :mod:`ralph.pipeline.integration_owner` re-exports. Keeping the enum
in its own module preserves the one-public-class-per-module repo-structure
policy; every consumer still imports it through the owner module.
"""

from __future__ import annotations

from enum import StrEnum


class RecordFactOutcome(StrEnum):
    """Typed fail-closed verdict for durable record facts the owner cannot express.

    No live observation can prove these outcomes; they are statements about
    DURABLE STATE, overlaid on the owner's mutation-ladder answer by
    :func:`ralph.pipeline._integration_record_facts.apply_record_facts`.
    Neither outcome ever ends the run blocked on a person: each maps to a
    cleanup-ladder rung in :data:`NEXT_ACTION_TO_LADDER_RUNG`, the ladder
    preserves the work to backup refs first and ends in rung 6 (hard
    reset) — never an operator hand-off.

    ``STALE_INTENT`` — the durable record cannot prove a next step the
    boundary can act on (a retained obligation with no record behind it,
    or a missing / malformed / expired bounded-retry deadline). The
    intent the record described is stale: Git outranks the record, so
    the seam preserves the work and re-enters the rebase-first ladder.

    ``DIAGNOSTIC_BUDGET_SPENT`` — the record's diagnostic repair-attempt
    flag is set: the repairs the record describes were tried and
    exhausted. Escalation continues through the same autonomous ladder.
    """

    STALE_INTENT = "stale_intent"
    DIAGNOSTIC_BUDGET_SPENT = "diagnostic_budget_spent"
