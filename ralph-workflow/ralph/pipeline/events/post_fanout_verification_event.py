from dataclasses import dataclass


@dataclass(frozen=True)
class PostFanoutVerificationEvent:
    """Event emitted after serialized workspace-wide verification runs post fan-out.

    Attributes:
        success: Whether verification passed (exit code 0).
        exit_code: The verification subprocess exit code, or None if not run.
        error: Human-readable error description when success=False, else None.
    """

    success: bool
    exit_code: int | None = None
    error: str | None = None
