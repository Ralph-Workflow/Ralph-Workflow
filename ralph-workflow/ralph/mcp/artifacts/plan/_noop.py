"""Noop short-circuit and error re-exports.

A plan with an explicit ``noop: true`` marker (or, defensively, a plan
with empty ``steps`` and empty ``work_units``) is treated as a no-op so
the executor short-circuits cleanly without consuming a malformed
artifact. The noop detection is co-located here for clarity; it
remains re-exported from the package namespace for backward
compatibility.

The module also re-exports ``PlanArtifactValidationError`` so callers
that prefer ``from ralph.mcp.artifacts.plan._noop import
PlanArtifactValidationError`` do not need a second import.
"""

from __future__ import annotations

from ralph.mcp.artifacts.plan._validation import (
    PlanArtifactValidationError,
    is_noop_plan,
)

__all__ = [
    "PlanArtifactValidationError",
    "is_noop_plan",
]
