from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

_SUBAGENT_SOURCES: frozenset[str] = frozenset(
    {
        "opencode",
        "claude",
        "pi",
        "agy",
        "generic",
        "claude_interactive",
        "codex",
        "nanocoder",
        "gemini",
        "cursor",
        "kimi",
    }
)

MAX_SUBAGENT_REGISTRY_ENTRIES: int = 1024


_IdentityMap = OrderedDict[int, "SubagentIdentity"]


@dataclass(frozen=True, slots=True)
class SubagentIdentity:
    pid: int
    source: Literal[
        "opencode",
        "claude",
        "pi",
        "agy",
        "generic",
        "claude_interactive",
        "codex",
        "nanocoder",
        "gemini",
        "cursor",
        "kimi",
    ]
    registered_at_monotonic: float
    label_prefix: str | None = None

    def __post_init__(self) -> None:
        if self.source not in _SUBAGENT_SOURCES:
            msg = (
                f"unknown subagent source {self.source!r}; expected one of"
                f" {sorted(_SUBAGENT_SOURCES)}"
            )
            raise ValueError(msg)
        if self.pid <= 0:
            msg = f"pid must be positive (got {self.pid})"
            raise ValueError(msg)


class SubagentPidRegistry:
    __slots__ = ("_identities", "_known_pids_cache", "_lock")

    def __init__(self) -> None:
        self._identities: _IdentityMap = OrderedDict()
        self._lock = threading.Lock()
        self._known_pids_cache: frozenset[int] | None = None

    def register(
        self,
        pid: int,
        source: Literal[
            "opencode",
            "claude",
            "pi",
            "agy",
            "generic",
            "claude_interactive",
            "codex",
            "nanocoder",
            "gemini",
            "cursor",
            "kimi",
        ],
        label_prefix: str | None = None,
        *,
        now: float | None = None,
    ) -> SubagentIdentity:
        if source not in _SUBAGENT_SOURCES:
            msg = f"unknown subagent source {source!r}; expected one of {sorted(_SUBAGENT_SOURCES)}"
            raise ValueError(msg)
        with self._lock:
            existing = self._identities.get(pid)
            if existing is not None:
                return existing
            if now is None:
                now = time.monotonic()
            identity = SubagentIdentity(
                pid=pid,
                source=source,
                registered_at_monotonic=now,
                label_prefix=label_prefix,
            )
            self._identities[pid] = identity
            while len(self._identities) > MAX_SUBAGENT_REGISTRY_ENTRIES:
                self._identities.popitem(last=False)
            self._known_pids_cache = None
            return identity

    def unregister(self, pid: int) -> None:
        with self._lock:
            if pid in self._identities:
                del self._identities[pid]
                self._known_pids_cache = None

    def known_pids(self) -> frozenset[int]:
        with self._lock:
            cached = self._known_pids_cache
            if cached is not None:
                return cached
            pids = frozenset(self._identities.keys())
            self._known_pids_cache = pids
            return pids

    def snapshot(self) -> tuple[SubagentIdentity, ...]:
        with self._lock:
            return tuple(self._identities.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._identities)

    def __contains__(self, pid: object) -> bool:
        if not isinstance(pid, int):
            return False
        with self._lock:
            return pid in self._identities


__all__ = [
    "MAX_SUBAGENT_REGISTRY_ENTRIES",
    "SubagentIdentity",
    "SubagentPidRegistry",
]
