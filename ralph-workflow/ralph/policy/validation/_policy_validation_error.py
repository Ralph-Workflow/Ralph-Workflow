"""PolicyValidationError exception and PolicyViolation alias."""

from __future__ import annotations


class PolicyValidationError(Exception):
    """Raised when a policy validation rule is violated.

    Attributes:
        message: Human-readable error message describing the validation failure.
        source: Which policy area failed (optional).
    """

    def __init__(self, message: str, source: str | None = None) -> None:
        self.message = message
        self.source = source
        super().__init__(message)


PolicyViolation = PolicyValidationError
