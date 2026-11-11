
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry

    from ._subagent_output_capture import SubagentOutputCapture


@runtime_checkable
class DiscoveryStrategy(Protocol):

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        ...


class NullDiscoveryStrategy:

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        _ = host_pid
        return {}


class RegistryBackedSubagentOutputCapture:

    def __init__(
        self,
        registry: ChildLivenessRegistry,
        child_id: str,
        scope_prefix: str,
    ) -> None:
        self._registry = registry
        self._child_id = child_id
        self._scope_prefix = scope_prefix
        self._last_progress_at: float | None = None
        self._last_heartbeat_at: float | None = None
        self._last_terminal_state: str | None = None
        self._last_progress_phase: str | None = None

    def read_lines(self, worker_id: str) -> list[str]:
        if worker_id != self._child_id:
            return []

        record = self._registry._records.get(self._child_id)
        if record is None:
            return []

        lines: list[str] = []

        if record.last_progress_at is not None and (
            record.last_progress_at != self._last_progress_at
            or (
                record.last_known_phase != self._last_progress_phase
                and record.last_progress_at != self._last_progress_at
            )
        ):
            phase = record.last_known_phase or "progress"
            lines.append(f"[subagent] progress: phase={phase}")
            self._last_progress_at = record.last_progress_at
            self._last_progress_phase = record.last_known_phase

        if record.last_heartbeat_at != self._last_heartbeat_at:
            lines.append("[subagent] heartbeat")
            self._last_heartbeat_at = record.last_heartbeat_at

        if record.terminal_state is not None and record.terminal_state != self._last_terminal_state:
            state = record.terminal_state
            lines.append(f"[subagent] terminal: state={state}")
            self._last_terminal_state = state

        return lines


class OpenCodeRegistryDiscoveryStrategy:

    def __init__(
        self,
        registry: ChildLivenessRegistry,
        scope_prefix: str = "",
    ) -> None:
        self._registry = registry
        self._scope_prefix = scope_prefix

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        _ = host_pid
        result: dict[str, SubagentOutputCapture] = {}
        now = self._registry._now()
        for child_id, record in self._registry._records.items():
            if not record.scope_prefix.startswith(self._scope_prefix):
                continue
            if record.terminal_state is not None:
                continue
            heartbeat_fresh = (
                record.last_heartbeat_at is not None
                and (now - record.last_heartbeat_at) <= self._registry._heartbeat_ttl
            )
            if heartbeat_fresh:
                pass
            elif record.last_progress_at is not None:
                progress_age = now - record.last_progress_at
                if progress_age > self._registry._progress_ttl:
                    continue
            else:
                label_age = now - record.started_at
                if label_age > self._registry._stale_label_ttl:
                    continue
            result[child_id] = RegistryBackedSubagentOutputCapture(
                self._registry,
                child_id,
                self._scope_prefix,
            )
        return result


__all__ = [
    "DiscoveryStrategy",
    "NullDiscoveryStrategy",
    "OpenCodeRegistryDiscoveryStrategy",
    "RegistryBackedSubagentOutputCapture",
]
