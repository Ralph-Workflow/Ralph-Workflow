from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


def _checkout_key(root: Path | str) -> str:
    path = Path(root)
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return str(path.absolute())


def same_checkout(first: Path | str, second: Path | str) -> bool:
    return _checkout_key(first) == _checkout_key(second)


class AgentPhaseActivity:
    def __init__(self) -> None:
        self._state = threading.Lock()
        self._mutations = threading.RLock()
        self._active: dict[str, int] = {}

    @contextmanager
    def agent_phase(self, root: Path | str) -> Iterator[None]:
        key = _checkout_key(root)
        entered = False
        try:
            with self._mutations, self._state:
                self._active[key] = self._active.get(key, 0) + 1
                entered = True
            yield
        finally:
            if entered:
                with self._state:
                    remaining = self._active.get(key, 0) - 1
                    if remaining > 0:
                        self._active[key] = remaining
                    else:
                        self._active.pop(key, None)

    def is_active(self, root: Path | str) -> bool:
        key = _checkout_key(root)
        with self._state:
            return key in self._active

    @contextmanager
    def mutation_window(self) -> Iterator[None]:
        with self._mutations:
            yield


_PROCESS_AGENT_PHASES = AgentPhaseActivity()


def process_agent_phases() -> AgentPhaseActivity:
    return _PROCESS_AGENT_PHASES


__all__ = ["AgentPhaseActivity", "process_agent_phases", "same_checkout"]
