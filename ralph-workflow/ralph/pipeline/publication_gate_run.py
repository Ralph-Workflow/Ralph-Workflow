from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublicationGateRun:
    exit_code: int
    output: str
    timed_out: bool = False
    elapsed_seconds: float = 0.0


__all__ = ["PublicationGateRun"]
