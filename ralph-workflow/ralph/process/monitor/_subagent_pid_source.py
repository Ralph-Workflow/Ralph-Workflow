from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SubagentPidSource(Protocol):
    def known_subagent_pids(self) -> set[int]: ...
