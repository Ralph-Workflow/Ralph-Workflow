"""Workspace change kind enum and default policy constants.

Defined in a leaf module (no internal Ralph imports) so both
``ralph.agents.idle_watchdog.timeout_policy`` and
``ralph.agents.invoke._workspace_change_classifier`` can import from
it without triggering a circular import via
``ralph.agents.invoke.__init__``.

The enum and the default-weights dict are the canonical contract for
the binary-weight workspace channel: 0.0 means "drop the event"
(it does NOT defer the NO_OUTPUT_DEADLINE verdict); 1.0 means
"full activity".
"""

from __future__ import annotations

from enum import StrEnum


class WorkspaceChangeKind(StrEnum):
    """Kind of a workspace file change for activity classification."""

    SOURCE = "source"
    LOG = "log"
    CACHE = "cache"
    ARTIFACT = "artifact"
    OTHER = "other"


#: Conservative default policy: only source-code changes count as activity.
#: Log, cache, artifact, and other file changes are dropped by default. This
#: is the policy wired into the production WorkspaceMonitor by default;
#: operators who relied on log-file activity to defer the verdict can opt in
#: by overriding the dict (e.g. ``{"log": 1.0, "source": 1.0}``).
DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS: dict[str, float] = {
    "source": 1.0,
    "log": 0.0,
    "cache": 0.0,
    "artifact": 0.0,
    "other": 0.0,
}


__all__ = [
    "DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS",
    "WorkspaceChangeKind",
]
