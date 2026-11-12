from __future__ import annotations

from enum import StrEnum


class RebaseOutcomeKind(StrEnum):
    REBASED = "rebased"
    MERGED = "merged"
    FAST_FORWARDED = "fast_forwarded"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
    RECOVERED = "recovered"
    DIAGNOSING = "diagnosing"
    EVIDENCE_DIAGNOSIS = "evidence_diagnosis"
    INTERVENTION_REQUIRED = "intervention_required"
    DIAGNOSTIC_INTERVENTION_REQUIRED = "diagnostic_intervention_required"
    LADDER_ESCALATION = "ladder_escalation"
    TERMINAL = "terminal"
    COMPLETED = "completed"
    REMOTE_PUSHED = "remote_pushed"
    REMOTE_FAILED = "remote_failed"
    INTEGRATING = "integrating"
    LANDING = "landing"
    PUBLISHING = "publishing"
    CONFLICT_RESOLUTION = "conflict_resolution"
    WAITING = "waiting"
    UNKNOWN = "unknown"

    def is_terminal(self) -> bool:
        return self in (
            RebaseOutcomeKind.TERMINAL,
            RebaseOutcomeKind.COMPLETED,
            RebaseOutcomeKind.REMOTE_PUSHED,
        )

    @classmethod
    def from_legacy(cls, action: str | None) -> RebaseOutcomeKind | None:
        if action is None:
            return None
        normalized = action.strip().lower()
        if normalized == "fast_forward":
            return cls.FAST_FORWARDED
        for member in cls:
            if member.value == normalized:
                return member
        return cls.UNKNOWN


__all__ = ["RebaseOutcomeKind"]
