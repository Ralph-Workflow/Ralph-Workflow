"""ProcessResult — captured result from a completed process."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessResult:
    """Captured result from a completed process."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        """Return ``True`` when the process exited successfully."""
        return self.returncode == 0


__all__ = ["ProcessResult"]
