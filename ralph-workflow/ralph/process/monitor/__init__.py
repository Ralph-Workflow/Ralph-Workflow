"""Process monitoring for agent-agnostic subagent discovery and output capture.

Discovery strategies are documentation-grounded only. When a path cannot be
established from official docs, the strategy reports an empty mapping rather
than inventing a convention.

Cross-transport contract
------------------------

For each supported transport the watchdog must surface what every active
subagent is doing in real time. The transport-specific source of that
evidence differs:

* **OpenCode** emits structured child lifecycle events on stdout that the
  ``OpenCodeExecutionStrategy`` ingests into a per-invocation
  ``ChildLivenessRegistry``. The factory returns
  :class:`OpenCodeRegistryDiscoveryStrategy` for the OPENCODE transport
  when a registry is provided so a per-child
  :class:`RegistryBackedSubagentOutputCapture` can surface textual
  descriptions of progress / heartbeat / terminal events.

* **Claude / Claude-interactive / Codex / Nanocoder / Generic / Agy / Pi**
  do not document a stable per-worker subagent log path. The factory
  returns :class:`NullDiscoveryStrategy` for these transports; real-time
  subagent visibility flows through the cross-transport subagent activity
  sink (:meth:`IdleWatchdog.record_subagent_work`) which the
  line-loop observes invoke on every child-signal line.
"""

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
    make_gemini_subagent_pid_source,
    make_generic_subagent_pid_source,
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
    "make_gemini_subagent_pid_source",
    "make_generic_subagent_pid_source",
    "make_opencode_subagent_pid_source",
    "make_pi_subagent_pid_source",
    "role_classifier_for_transport",
]
