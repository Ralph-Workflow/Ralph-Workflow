from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _ProbeResult:
    healthy: bool
    latency_ms: float = 0.0
    reason: str = ""


__all__ = ["_ProbeResult"]
