"""Structured root cause for an integration or conflict-resolution failure.

The git layer produces this cause (:class:`ralph.git.merge.MergeResult`) and
the pipeline persists it (:class:`ralph.pipeline.rebase_state.RebaseState`).
It lives here so the git layer never imports :mod:`ralph.pipeline`, whose
package initialiser imports the git layer back.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ResolutionFailureCause"]


class ResolutionFailureCause(StrEnum):
    """Structured root cause for an integration or conflict resolution failure."""

    NONE = "NONE"
    AGENT_FAILURE = "AGENT_FAILURE"
    UNRESOLVED_CONFLICT = "UNRESOLVED_CONFLICT"
    CONTEXT_FAILURE = "CONTEXT_FAILURE"
