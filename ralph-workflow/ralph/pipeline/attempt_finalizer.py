from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.recovery.attempt_outcome import AttemptOutcome

if TYPE_CHECKING:
    from ralph.recovery.dead_agent_verdict import TypedSignal


@dataclass(frozen=True)
class AttemptFinalization:
    attempt_ordinal: int
    attempt_failed: bool
    exc: BaseException | None
    workspace_changed: bool | None
    typed_signal: TypedSignal | None
    retryable_reason: str | None


AttemptFinalizer = Callable[[AttemptFinalization], AttemptOutcome]


__all__ = ("AttemptFinalization", "AttemptFinalizer")
