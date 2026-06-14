"""Claude Code subagent output discovery strategy.

Claude Code stores session metadata under ``.claude/`` in the working directory.
The public repository exposes session metadata at ``.claude/sessions/<number>.json``
and transcripts at ``.claude/projects/<project-id>/<session-uuid>.jsonl``
(Context7 /anthropics/claude-code, accessed 2026-06-14). Subagent worker output
is written to per-worker log files under the session directory. The exact
``worker-*/log.txt`` convention is not explicitly documented in the public
CHANGELOG or README, so this strategy treats the channel as available only when
the expected directory layout is actually present on disk.

Documentation references:
  - https://github.com/anthropics/claude-code (session metadata and transcripts)
  - Context7 /anthropics/claude-code, accessed 2026-06-14
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ._discovery_strategy import DiscoveryStrategy
from ._subagent_output_capture import FileSubagentOutputCapture

if TYPE_CHECKING:
    from ._subagent_output_capture import SubagentOutputCapture

_MIN_CLAUDE_PATH_PARTS = 5


class ClaudeCodeSubagentOutputDiscovery(DiscoveryStrategy):
    """Discover Claude Code subagent worker log files.

    Looks for ``.claude/session/*/worker-*/log.txt`` relative to the current
    working directory. The session directory name is treated as the session
    identifier; each ``worker-*`` directory name becomes the worker identifier.

    If no matching files are found, the strategy returns an empty mapping
    rather than inventing paths.
    """

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return worker_id -> capture for Claude Code subagent logs."""
        del host_pid  # Claude Code logs are located by session directory, not PID.
        paths = Path().glob(".claude/session/*/worker-*/log.txt")
        result: dict[str, SubagentOutputCapture] = {}
        for path in paths:
            # worker_id includes session and worker so it is unique.
            parts = path.parts
            worker_id = (
                f"{parts[-3]}/{parts[-2]}"
                if len(parts) >= _MIN_CLAUDE_PATH_PARTS
                else str(path)
            )
            result[worker_id] = FileSubagentOutputCapture(str(path))
        return result
