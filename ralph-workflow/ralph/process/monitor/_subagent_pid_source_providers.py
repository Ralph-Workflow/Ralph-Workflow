
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog import SubagentPidRegistry
    from ralph.process.monitor import SubagentPidSource


class _RegistryBackedSubagentPidSource:

    __slots__ = ("_registry", "_source_label")

    def __init__(self, registry: SubagentPidRegistry, source_label: str) -> None:
        self._registry = registry
        self._source_label = source_label

    def known_subagent_pids(self) -> set[int]:
        return {
            identity.pid
            for identity in self._registry.snapshot()
            if identity.source == self._source_label
        }

    def __repr__(self) -> str:
        return f"_RegistryBackedSubagentPidSource(source={self._source_label!r})"


def make_opencode_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "opencode")


def make_claude_subagent_pid_source(
    registry: SubagentPidRegistry,
) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "claude")


def make_pi_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "pi")


def make_agy_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "agy")


def make_claude_interactive_subagent_pid_source(
    registry: SubagentPidRegistry,
) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "claude_interactive")


def make_codex_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "codex")


def make_generic_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "generic")


def make_nanocoder_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "nanocoder")


def make_cursor_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "cursor")


def make_kimi_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    return _RegistryBackedSubagentPidSource(registry, "kimi")


__all__ = [
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
]
