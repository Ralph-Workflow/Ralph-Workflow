from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InteractiveCompletionObserved:
    grace_s: float
