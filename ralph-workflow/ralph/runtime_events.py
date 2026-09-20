"""Invocation-local runtime-event attribution for causal failure records."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke._failure_origin import FailureOrigin


@dataclass
class RuntimeEventRecorder:
    """Retain the latest bounded runtime event for one invocation."""

    _latest: FailureOrigin | None = None
    detail: str | None = None

    def record(self, origin: FailureOrigin, detail: str) -> None:
        self._latest = origin
        self.detail = detail

    def latest(self) -> FailureOrigin | None:
        return self._latest


_RECORDER: ContextVar[RuntimeEventRecorder | None] = ContextVar("runtime_event_recorder", default=None)


@contextmanager
def runtime_event_scope(recorder: RuntimeEventRecorder) -> Iterator[RuntimeEventRecorder]:
    """Make ``recorder`` available only for this invocation's context."""
    token = _RECORDER.set(recorder)
    try:
        yield recorder
    finally:
        _RECORDER.reset(token)


def record_runtime_event(origin: FailureOrigin, detail: str) -> None:
    """Record a real runtime event when an invocation scope is active."""
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder.record(origin, detail)


def current_runtime_event() -> FailureOrigin | None:
    """Return the current invocation's most recent runtime event."""
    recorder = _RECORDER.get()
    return recorder.latest() if recorder is not None else None


__all__ = [
    "RuntimeEventRecorder",
    "current_runtime_event",
    "record_runtime_event",
    "runtime_event_scope",
]
