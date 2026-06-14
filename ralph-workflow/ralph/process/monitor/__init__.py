"""Process monitoring for agent-agnostic subagent discovery and output capture."""

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
