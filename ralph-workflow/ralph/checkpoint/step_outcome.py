"""Outcome metadata for checkpoint execution steps."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StepOutcome:
    """Outcome metadata for a single execution step."""

    kind: str
    output: str | None = None
    files_modified: list[str] = field(default_factory=list)
    exit_code: int | None = None
    recoverable: bool | None = None
    error: str | None = None
    completed: str | None = None
    remaining: str | None = None
    reason: str | None = None

    @classmethod
    def success(
        cls,
        output: str | None = None,
        files_modified: list[str] | None = None,
    ) -> StepOutcome:
        """Create a success outcome."""
        return cls(
            kind="success",
            output=output,
            files_modified=files_modified or [],
            exit_code=0,
        )

    @classmethod
    def failure(cls, error: str, *, recoverable: bool) -> StepOutcome:
        """Create a failure outcome."""
        return cls(kind="failure", error=error, recoverable=recoverable)

    @classmethod
    def partial(cls, completed: str, remaining: str) -> StepOutcome:
        """Create a partial outcome."""
        return cls(kind="partial", completed=completed, remaining=remaining)

    @classmethod
    def skipped(cls, reason: str) -> StepOutcome:
        """Create a skipped outcome."""
        return cls(kind="skipped", reason=reason)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe dictionary representation."""
        return {
            "kind": self.kind,
            "output": self.output,
            "files_modified": list(self.files_modified),
            "exit_code": self.exit_code,
            "recoverable": self.recoverable,
            "error": self.error,
            "completed": self.completed,
            "remaining": self.remaining,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> StepOutcome:
        """Construct StepOutcome from dictionary representation."""
        files = data.get("files_modified")
        output_val = data.get("output")
        exit_code_val = data.get("exit_code")
        recoverable_val = data.get("recoverable")
        error_val = data.get("error")
        completed_val = data.get("completed")
        remaining_val = data.get("remaining")
        reason_val = data.get("reason")
        return cls(
            kind=str(data.get("kind", "unknown")),
            output=output_val if isinstance(output_val, str) else None,
            files_modified=[str(f) for f in files] if isinstance(files, list) else [],
            exit_code=exit_code_val if isinstance(exit_code_val, int) else None,
            recoverable=recoverable_val if isinstance(recoverable_val, bool) else None,
            error=error_val if isinstance(error_val, str) else None,
            completed=completed_val if isinstance(completed_val, str) else None,
            remaining=remaining_val if isinstance(remaining_val, str) else None,
            reason=reason_val if isinstance(reason_val, str) else None,
        )
