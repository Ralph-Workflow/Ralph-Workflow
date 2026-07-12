"""Project-policy readiness status enum.

The four canonical outcome states of the readiness preflight. The string
values are the public display labels used by the CLI status line and by
external reports; the values are part of the wire contract for downstream
tooling that reads the cache or the preflight report, so do not rename them
without a coordinated schema bump.
"""

from __future__ import annotations

from enum import StrEnum


class ReadinessStatus(StrEnum):
    """Canonical outcome states of the readiness preflight."""

    READY = "ready"
    SKIPPED = "skipped"
    REMEDIATION_REQUIRED = "remediation-required"
    BLOCKED = "blocked"


__all__ = ["ReadinessStatus"]
