"""OpenCode subagent output discovery strategy.

OpenCode uses ``.opencode/`` as its data directory (Context7
``/opencode-ai/opencode``, accessed 2026-06-14). The public documentation
covers the available tools (glob, grep, ls, view, write, edit, patch,
diagnostics) and the permission/bash/write services, but it does not
document a stable per-worker subagent output log path such as
``.agent/workers/*/output.log``.

Because the strategy must be grounded in documented behavior (AC-11), this
implementation reports the subagent-output channel as unavailable rather than
guessing a convention.

Documentation references:
  - https://github.com/opencode-ai/opencode
  - Context7 ``/opencode-ai/opencode``, accessed 2026-06-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._discovery_strategy import DiscoveryStrategy

if TYPE_CHECKING:
    from ._subagent_output_capture import SubagentOutputCapture


class OpencodeSubagentOutputDiscovery(DiscoveryStrategy):
    """OpenCode subagent output discovery.

    The documented OpenCode surface does not expose a stable per-worker
    subagent log path. The strategy therefore returns an empty mapping, which
    causes the watchdog to treat subagent output as not observable for this
    agent and fall back to the other evidence channels it does have.
    """

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return an empty mapping because the log path is not documented."""
        _ = host_pid
        return {}
