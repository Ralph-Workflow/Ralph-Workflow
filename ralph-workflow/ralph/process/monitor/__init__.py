from __future__ import annotations

from ._default_monitor import DefaultProcessMonitor
from ._discovery_strategy import (
    DiscoveryStrategy,
    NullDiscoveryStrategy,
    OpenCodeRegistryDiscoveryStrategy,
    RegistryBackedSubagentOutputCapture,
)
from ._process_monitor import ClassifiedProcess, ProcessMonitor, ProcessRole
from ._role_classifier import RoleClassifier, role_classifier_for_transport
from ._subagent_output_capture import FileSubagentOutputCapture, SubagentOutputCapture
from ._subagent_pid_source import SubagentPidSource
from ._subagent_pid_source_providers import (
    make_agy_subagent_pid_source,
    make_claude_interactive_subagent_pid_source,
    make_claude_subagent_pid_source,
    make_codex_subagent_pid_source,
    make_cursor_subagent_pid_source,
    make_generic_subagent_pid_source,
    make_kimi_subagent_pid_source,
    make_nanocoder_subagent_pid_source,
    make_opencode_subagent_pid_source,
    make_pi_subagent_pid_source,
)

__all__ = [
    "ClassifiedProcess",
    "DefaultProcessMonitor",
    "DiscoveryStrategy",
    "FileSubagentOutputCapture",
    "NullDiscoveryStrategy",
    "OpenCodeRegistryDiscoveryStrategy",
    "ProcessMonitor",
    "ProcessRole",
    "RegistryBackedSubagentOutputCapture",
    "RoleClassifier",
    "SubagentOutputCapture",
    "SubagentPidSource",
    "make_agy_subagent_pid_source",
    "make_claude_interactive_subagent_pid_source",
    "make_claude_subagent_pid_source",
    "make_codex_subagent_pid_source",
    "make_cursor_subagent_pid_source",
    "make_generic_subagent_pid_source",
    "make_kimi_subagent_pid_source",
    "make_nanocoder_subagent_pid_source",
    "make_opencode_subagent_pid_source",
    "make_pi_subagent_pid_source",
    "role_classifier_for_transport",
]
