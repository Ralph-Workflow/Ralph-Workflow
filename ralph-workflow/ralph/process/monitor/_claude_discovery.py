"""Claude Code subagent output discovery strategy.

Claude Code stores session metadata under ``.claude/`` in the working directory.
The public repository exposes session metadata at
``.claude/sessions/<number>.json`` and transcripts at
``.claude/projects/<project-id>/<session-uuid>.jsonl`` (Context7
``/anthropics/claude-code``, accessed 2026-06-14). A security-guidance plugin
also writes its own log to ``~/.claude/security/log.txt``.

No official Claude Code documentation documents a stable, per-worker subagent
output log path such as ``worker-*/log.txt``. Because the strategy must be
grounded in documented behavior (AC-11), this implementation reports the
subagent-output channel as unavailable rather than guessing a convention.

Documentation references:
  - https://github.com/anthropics/claude-code
  - Context7 ``/anthropics/claude-code``, accessed 2026-06-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._discovery_strategy import DiscoveryStrategy

if TYPE_CHECKING:
    from ._subagent_output_capture import SubagentOutputCapture


class ClaudeCodeSubagentOutputDiscovery(DiscoveryStrategy):
    """Claude Code subagent output discovery.

    The documented Claude Code surface does not expose a stable per-worker
    subagent log path. The strategy therefore returns an empty mapping, which
    causes the watchdog to treat subagent output as not observable for this
    agent and fall back to the other evidence channels it does have.
    """

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return an empty mapping because the log path is not documented."""
        _ = host_pid
        return {}
