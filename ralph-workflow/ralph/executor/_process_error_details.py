"""ProcessErrorDetails — structured error details captured from a failed process launch."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessErrorDetails:
    """Structured error details captured from a failed process launch."""

    timed_out: bool = False
    timeout: float | None = None
    stdout: str = ""
    stderr: str = ""


__all__ = ["ProcessErrorDetails"]
