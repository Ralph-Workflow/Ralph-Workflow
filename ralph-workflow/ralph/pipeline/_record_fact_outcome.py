from __future__ import annotations

from enum import StrEnum


class RecordFactOutcome(StrEnum):
    STALE_INTENT = "stale_intent"
    DIAGNOSTIC_BUDGET_SPENT = "diagnostic_budget_spent"
