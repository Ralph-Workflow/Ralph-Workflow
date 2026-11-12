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

BACKOFF_BASE_SECONDS: Final[float] = 1.0
BACKOFF_CAP_SECONDS: Final[float] = 60.0

_TERMINAL_PREFIXES: Final[tuple[str, ...]] = ("succeeded", "failed", "impossible")

_RUNG_ACTION_VALUE: Final[dict[CleanupLadderRung, str]] = {
    CleanupLadderRung.NORMAL_CLEANUP: NextAction.SAVE_AND_RESTORE.value,
    CleanupLadderRung.ALTERNATE_PRESERVATION: NextAction.PRESERVE_VIA_TEMP_INDEX.value,
    CleanupLadderRung.QUARANTINE: NextAction.QUARANTINE_AND_RESTORE.value,
    CleanupLadderRung.FRESH_WORKTREE: NextAction.REBUILD_IN_FRESH_WORKTREE.value,
    CleanupLadderRung.ENVIRONMENT_DEGRADE: NextAction.ADAPT_ENVIRONMENT_AND_RETRY.value,
    CleanupLadderRung.LAST_RESORT: NextAction.LAST_RESORT_RECOVERY.value,
}

_LADDER_ACTION_VALUES: Final[frozenset[str]] = frozenset(_RUNG_ACTION_VALUE.values())


def _record_evidence(ledger: AttemptLedger, key: str, value: str) -> None:
    state = ledger.read()
    evidence = dict(state.ladder_evidence)
    evidence[key] = value
    ledger.persist(replace(state, ladder_evidence=evidence))


def _rung_key(rung: CleanupLadderRung) -> str:
    return f"rung:{_RUNG_ACTION_VALUE[rung]}"


def _is_terminal(value: str | None) -> bool:
    return value is not None and value.startswith(_TERMINAL_PREFIXES)


def _rung_attempt_satisfied(value: str | None) -> bool:
    if _is_terminal(value):
        return True
    if value is None or not value.startswith("waiting:"):
        return False
    return f"delay={BACKOFF_CAP_SECONDS}" in value
