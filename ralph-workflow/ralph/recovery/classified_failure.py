from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .attempt_facts import AttemptFacts
    from .failure_category import FailureCategory
    from .unavailability_reason import UnavailabilityReason


@dataclass(frozen=True)
class ClassifiedFailure:
    category: FailureCategory
    reason: str
    attributed_agent: str | None
    attributed_phase: str
    counts_against_budget: bool
    original_exception: BaseException | None
    raw_message: str
    reset_session: bool = field(default=False)
    reset_tool_registry: bool = field(default=False)
    is_unavailable: bool = field(default=False)
    watchdog_reason: str | None = field(default=None)
    unavailability_reason: UnavailabilityReason | None = field(default=None)
    resumable_session_id: str | None = field(default=None)
    typed_signal: str | None = field(default=None)
    typed_signal_provider_originated: bool = field(default=False)
    facts: AttemptFacts | None = field(default=None)
