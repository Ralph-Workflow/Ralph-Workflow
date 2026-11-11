
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessErrorDetails:

    timed_out: bool = False
    timeout: float | None = None
    stdout: str = ""
    stderr: str = ""


__all__ = ["ProcessErrorDetails"]
