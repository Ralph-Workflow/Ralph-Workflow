"""Protocol for sources that know which descendant PIDs are spawned subagents.

A ``SubagentPidSource`` answers the question: "for the agent running under
``host_pid``, which descendant PIDs are known to be spawned subagents doing
delegated work?"  The source is injected into ``DefaultProcessMonitor`` so
transport-specific discovery (for example, OpenCode's structured child events
that carry PIDs) can promote descendants to ``ProcessRole.SPAWNED_SUBAGENT``
without the monitor knowing the transport details.

When no source is injected, or when the source returns an empty set, the
monitor falls back to the documentation-grounded ``role_classifier``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SubagentPidSource(Protocol):
    """Provides the set of descendant PIDs known to be spawned subagents."""

    def known_subagent_pids(self) -> set[int]:
        """Return the PIDs currently known to be spawned subagents.

        The returned set may be empty. Implementations should swallow their
        own errors and return an empty set rather than raise, because process
        monitoring must not crash the watchdog when a discovery source fails.
        """
        ...
