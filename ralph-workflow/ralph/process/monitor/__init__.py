"""Process monitoring for agent-agnostic subagent discovery and output capture.

Supported transports and their subagent-output discovery status:

- CLAUDE / CLAUDE_INTERACTIVE: ClaudeCodeSubagentOutputDiscovery. The
  documented Claude Code surface does not expose a stable per-worker subagent
  log path, so the strategy returns an empty mapping (channel unavailable).
- OPENCODE: OpencodeSubagentOutputDiscovery. The documented OpenCode surface
  does not expose a stable per-worker subagent log path, so the strategy
  returns an empty mapping (channel unavailable).
- CODEX, NANOCODER, GENERIC, AGY: no documented subagent output path. The
  channel is explicitly unavailable; the watchdog degrades gracefully to
  stdout, MCP tool-call, and workspace evidence for these transports.

Discovery strategies are documentation-grounded only. When a path cannot be
established from official docs, the strategy reports an empty mapping rather
than inventing a convention.
"""

from __future__ import annotations

from ._claude_discovery import ClaudeCodeSubagentOutputDiscovery
from ._default_monitor import DefaultProcessMonitor
from ._discovery_strategy import DiscoveryStrategy
from ._opencode_discovery import OpencodeSubagentOutputDiscovery
from ._process_monitor import ClassifiedProcess, ProcessMonitor, ProcessRole
from ._subagent_output_capture import FileSubagentOutputCapture, SubagentOutputCapture

__all__ = [
    "ClassifiedProcess",
    "ClaudeCodeSubagentOutputDiscovery",
    "DefaultProcessMonitor",
    "DiscoveryStrategy",
    "FileSubagentOutputCapture",
    "OpencodeSubagentOutputDiscovery",
    "ProcessMonitor",
    "ProcessRole",
    "SubagentOutputCapture",
]
