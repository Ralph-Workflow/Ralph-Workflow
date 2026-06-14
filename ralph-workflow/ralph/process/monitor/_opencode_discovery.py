"""OpenCode subagent output discovery strategy.

OpenCode uses ``.opencode/`` as its data directory and ``.agent/`` for project-local
agent state (Context7 /opencode-ai/opencode, accessed 2026-06-14). Subagent worker
output is written to per-worker log files under ``.agent/workers/*/output.log``.
The exact path convention is derived from OpenCode's project-local agent state
layout; this strategy treats the channel as available only when the expected
directory layout is actually present on disk.

Documentation references:
  - https://github.com/opencode-ai/opencode (configuration and data directories)
  - Context7 /opencode-ai/opencode, accessed 2026-06-14
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ._discovery_strategy import DiscoveryStrategy
from ._subagent_output_capture import FileSubagentOutputCapture

if TYPE_CHECKING:
    from ._subagent_output_capture import SubagentOutputCapture

_MIN_OPENCODE_PATH_PARTS = 3


class OpencodeSubagentOutputDiscovery(DiscoveryStrategy):
    """Discover OpenCode subagent worker log files.

    Looks for ``.agent/workers/*/output.log`` relative to the current working
    directory. Each worker directory name becomes the worker identifier.

    If no matching files are found, the strategy returns an empty mapping
    rather than inventing paths.
    """

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        """Return worker_id -> capture for OpenCode subagent logs."""
        del host_pid  # OpenCode logs are located by worker directory, not PID.
        paths = Path().glob(".agent/workers/*/output.log")
        result: dict[str, SubagentOutputCapture] = {}
        for path in paths:
            parts = path.parts
            worker_id = (
                parts[-2]
                if len(parts) >= _MIN_OPENCODE_PATH_PARTS
                else str(path)
            )
            result[worker_id] = FileSubagentOutputCapture(str(path))
        return result
