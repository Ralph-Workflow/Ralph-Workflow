from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditCorrelation:
    run_id: str | None = None
    generation: int | None = None
    drain: str | None = None
    policy_mode: str | None = None


__all__ = ["AuditCorrelation"]
