"""The durable-evidence vocabulary of the dirty-tree fallback ladder.

Every rung of the cleanup ladder records its intent BEFORE it acts and
its outcome AFTER, in the durable attempt ledger. This module owns the
vocabulary those records are written in: the rung -> action table, the
prefixes that mark a finished outcome, the capped backoff schedule the
degrade rung waits and records, and the helpers that read and write
the evidence.

The rungs themselves stay in :mod:`ralph.pipeline.integration_ladder`;
this module owns the ledger they write into, not the work they do.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from ralph.pipeline.integration_decision import (
    CleanupLadderRung,
    NextAction,
)

if TYPE_CHECKING:
    from ralph.pipeline.integration_attempt_ledger import (
        AttemptLedger,
    )

#: Capped exponential backoff for the degrade rung: ``base * 2**attempt``
#: capped at ``cap`` seconds, slept on the INJECTABLE clock.
BACKOFF_BASE_SECONDS: Final[float] = 1.0
BACKOFF_CAP_SECONDS: Final[float] = 60.0

#: Terminal evidence values: a rung whose recorded value starts with one
#: of these has finished (successfully or not) and is never repeated.
_TERMINAL_PREFIXES: Final[tuple[str, ...]] = ("succeeded", "failed", "impossible")

#: Cleanup-ladder rung -> the decision-table action value the rung
#: implements; the same string is the ``rung:<action>`` evidence key.
_RUNG_ACTION_VALUE: Final[dict[CleanupLadderRung, str]] = {
    CleanupLadderRung.NORMAL_CLEANUP: NextAction.SAVE_AND_RESTORE.value,
    CleanupLadderRung.ALTERNATE_PRESERVATION: NextAction.PRESERVE_VIA_TEMP_INDEX.value,
    CleanupLadderRung.QUARANTINE: NextAction.QUARANTINE_AND_RESTORE.value,
    CleanupLadderRung.FRESH_WORKTREE: NextAction.REBUILD_IN_FRESH_WORKTREE.value,
    CleanupLadderRung.ENVIRONMENT_DEGRADE: NextAction.ADAPT_ENVIRONMENT_AND_RETRY.value,
    CleanupLadderRung.LAST_RESORT: NextAction.LAST_RESORT_RECOVERY.value,
}

#: Action values charged to the ledger for the cleanup ladder; their
#: repeat counts (plus the saturating aggregate) are the ladder budget.
_LADDER_ACTION_VALUES: Final[frozenset[str]] = frozenset(_RUNG_ACTION_VALUE.values())


def _record_evidence(ledger: AttemptLedger, key: str, value: str) -> None:
    """Record one ladder evidence entry durably (atomic, monotone merge)."""
    state = ledger.read()
    evidence = dict(state.ladder_evidence)
    evidence[key] = value
    ledger.persist(replace(state, ladder_evidence=evidence))


def _rung_key(rung: CleanupLadderRung) -> str:
    """Return the evidence key one rung records its intent and outcome under."""
    return f"rung:{_RUNG_ACTION_VALUE[rung]}"


def _is_terminal(value: str | None) -> bool:
    """True when a recorded value names a finished rung outcome."""
    return value is not None and value.startswith(_TERMINAL_PREFIXES)


def _rung_attempt_satisfied(value: str | None) -> bool:
    """True when a rung counts as "tried and failed or shown impossible".

    A terminal outcome satisfies precondition (a) directly. The degrade
    rung's steady state is ``waiting`` (it never "fails" — it waits on
    the machine), so a tick whose capped backoff has REACHED the cap
    counts as tried-and-failed: the environment had its full bounded
    wait across the cap and never healed, and without this rule the
    rungs precondition could never hold while rung 5 is the answer.
    """
    if _is_terminal(value):
        return True
    if value is None or not value.startswith("waiting:"):
        return False
    return f"delay={BACKOFF_CAP_SECONDS}" in value
