from __future__ import annotations


class MissingPlanHandoffError(ValueError):
    """Raised when a template requires an existing plan handoff that is absent."""
