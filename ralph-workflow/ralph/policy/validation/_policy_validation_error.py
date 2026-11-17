from __future__ import annotations


class PolicyValidationError(Exception):
    __slots__ = ("message", "source")

    def __init__(self, message: str, source: str | None = None) -> None:
        self.message = message
        self.source = source
        super().__init__(message)


class ArtifactContractMismatchError(PolicyValidationError):
    __slots__ = ("graded_type", "phase", "prompted_type")

    def __init__(
        self,
        phase: str,
        prompted_type: str | None,
        graded_type: str,
        message: str | None = None,
    ) -> None:
        self.phase = phase
        self.prompted_type = prompted_type
        self.graded_type = graded_type
        detail = message or (
            f"Phase '{phase}' artifact contract mismatch: prompted type is "
            f"'{prompted_type}' but graded type is '{graded_type}'"
        )
        super().__init__(detail)
