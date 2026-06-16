"""Discovery strategy protocol for finding subagent output streams.

A ``DiscoveryStrategy`` answers the question: "for the agent running under
``host_pid``, where (if anywhere) are its subagent output streams observable?"

Implementations are agent-specific and are injected into the process monitor
and the idle watchdog. If an implementation cannot establish the documented
output location for an agent, it must report an empty mapping rather than
inventing a path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ._subagent_output_capture import SubagentOutputCapture


@runtime_checkable
class DiscoveryStrategy(Protocol):
    """Agent-specific subagent output discovery.

    Implementations discover worker directories/log files for a particular
    agent CLI (Claude Code, OpenCode, etc.). They are documentation-grounded:
    every path they return must correspond to a documented convention for that
    agent.
    """

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return a mapping from worker_id to output capture for the host agent.

        Args:
            host_pid: PID of the top-level agent process.

        Returns:
            A dict mapping worker identifiers to ``SubagentOutputCapture``
            instances. An empty dict means the agent's subagent output is not
            observable (either because the agent does not expose it or because
            the documented location could not be confirmed).
        """
        ...


class NullDiscoveryStrategy:
    """Discovery strategy that returns an empty mapping.

    Used when no agent-specific discovery implementation exists. This is the
    default for all transports because no agent transport currently documents a
    stable per-worker subagent output log path.
    """

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return an empty mapping because no log path is documented."""
        _ = host_pid
        return {}
