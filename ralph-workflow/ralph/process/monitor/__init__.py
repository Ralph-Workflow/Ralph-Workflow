"""Process monitoring for agent-agnostic subagent discovery and output capture.

All supported transports (CLAUDE, CLAUDE_INTERACTIVE, CODEX, NANOCODER, GENERIC,
AGY, OPENCODE) do not document a stable per-worker subagent output log path.
The ``NullDiscoveryStrategy`` returns an empty mapping for all transports, and
the watchdog degrades gracefully to stdout, MCP tool-call, and workspace evidence.

Discovery strategies are documentation-grounded only. When a path cannot be
established from official docs, the strategy reports an empty mapping rather
than inventing a convention.
"""

from __future__ import annotations

from ._default_monitor import DefaultProcessMonitor
from ._discovery_strategy import DiscoveryStrategy, NullDiscoveryStrategy
from ._process_monitor import ClassifiedProcess, ProcessMonitor, ProcessRole
from ._role_classifier import RoleClassifier, role_classifier_for_transport
from ._subagent_output_capture import FileSubagentOutputCapture, SubagentOutputCapture
from ._subagent_pid_source import SubagentPidSource

__all__ = [
    "ClassifiedProcess",
    "DefaultProcessMonitor",
    "DiscoveryStrategy",
    "FileSubagentOutputCapture",
    "NullDiscoveryStrategy",
    "ProcessMonitor",
    "ProcessRole",
    "RoleClassifier",
    "SubagentOutputCapture",
    "SubagentPidSource",
    "role_classifier_for_transport",
]
