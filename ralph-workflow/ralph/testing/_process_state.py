from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProcessState:
    """Process state flags."""

    returncode: int | None = None
    terminated: bool = False
    killed: bool = False
