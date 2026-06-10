"""Health-probe result dataclass.

Split out of ``_fallback_http_server`` so it can be imported by
``_fallback_http_handler`` without creating a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _ProbeResult:
    """Result of a health-probe invocation. Used by the /health route."""

    healthy: bool
    latency_ms: float = 0.0
    reason: str = ""


__all__ = ["_ProbeResult"]
