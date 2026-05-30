"""Typed error raised when a reusable exec sandbox slot is already in use."""

from __future__ import annotations


class ExecSandboxBusyError(RuntimeError):
    """Raised when a reusable sandbox is already in use by another caller.

    Optional keyword fields enable structured, agent-actionable error messages.
    """

    def __init__(
        self,
        message: str = "",
        *,
        active_slots: int | None = None,
        max_slots: int | None = None,
        wait_time: float | None = None,
    ) -> None:
        super().__init__(message)
        self.active_slots = active_slots
        self.max_slots = max_slots
        self.wait_time = wait_time

    def __str__(self) -> str:
        base = super().__str__()
        if base and not self.active_slots:
            return base
        lines: list[str] = []
        if self.active_slots is not None and self.max_slots is not None:
            lines.append("Error: All sandbox slots are busy")
            lines.append(f"  Active slots: {self.active_slots} of {self.max_slots}")
        else:
            lines.append(base or "Error: Sandbox busy")
        if self.wait_time is not None:
            lines.append(f"  Wait time: {self.wait_time:.2f}s")
        lines.append(
            "  Suggestion: Retry after other exec commands complete, "
            "or reduce concurrent exec calls"
        )
        return "\n".join(lines)


__all__ = ["ExecSandboxBusyError"]
