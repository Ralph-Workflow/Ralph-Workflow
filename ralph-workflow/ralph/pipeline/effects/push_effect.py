from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PushEffect:
    remote: str
    branch: str | None = None
