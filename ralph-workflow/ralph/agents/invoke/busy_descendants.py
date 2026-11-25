from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import psutil

from ralph.agents.execution_state import AgentExecutionState
from ralph.process.child_liveness import AliveBy
from ralph.timeout_defaults import (
    EXEC_CPU_BUSY_MIN,
    EXEC_LIVENESS_EVIDENCE_WINDOW_S,
    EXEC_LIVENESS_SAMPLE_INTERVAL_S,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

MIN_MEASURED_SPAN_SECONDS: Final[float] = 5.0

_UPGRADABLE_ALIVE_BY: Final[frozenset[AliveBy | None]] = frozenset(
    {None, AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS}
)


@runtime_checkable
class _CpuTimes(Protocol):
    user: float
    system: float


class _Proc(Protocol):
    @property
    def pid(self) -> int: ...

    def children(self, *, recursive: bool = False) -> Sequence[_Proc]: ...

    def cpu_times(self) -> object: ...

    def create_time(self) -> float: ...


@dataclass(frozen=True)
class _Usage:
    cpu_seconds: float
    created_at: float


def _process(pid: int) -> _Proc:
    return psutil.Process(pid)


class BusyDescendantProbe:
    __slots__ = (
        "_clock",
        "_cpu_ratio",
        "_history",
        "_last_busy",
        "_last_sample_at",
        "_min_span_seconds",
        "_process",
        "_root_pid",
        "_sample_interval_seconds",
        "_window_seconds",
    )

    def __init__(
        self,
        *,
        process: Callable[[int], _Proc] = _process,
        clock: Callable[[], float] = time.time,
        window_seconds: float = EXEC_LIVENESS_EVIDENCE_WINDOW_S,
        min_span_seconds: float = MIN_MEASURED_SPAN_SECONDS,
        cpu_ratio: float = EXEC_CPU_BUSY_MIN,
        sample_interval_seconds: float = EXEC_LIVENESS_SAMPLE_INTERVAL_S,
    ) -> None:
        self._process = process
        self._clock = clock
        self._window_seconds = window_seconds
        self._min_span_seconds = min_span_seconds
        self._cpu_ratio = cpu_ratio
        self._sample_interval_seconds = sample_interval_seconds
        self._root_pid: int | None = None
        self._last_sample_at: float | None = None
        self._last_busy = False
        self._history: deque[tuple[float, dict[int, _Usage]]] = deque(maxlen=256)

    def busy(self, root_pid: int) -> bool:
        now = self._clock()
        if root_pid != self._root_pid:
            self._root_pid = root_pid
            self._history.clear()
            self._last_sample_at = None
        if (
            self._last_sample_at is not None
            and now - self._last_sample_at < self._sample_interval_seconds
        ):
            return self._last_busy
        self._last_sample_at = now
        usage = self._sample(root_pid)
        baseline = self._history[0] if self._history else None
        self._history.append((now, usage))
        while self._history and now - self._history[0][0] > self._window_seconds:
            self._history.popleft()
        self._last_busy = any(
            self._is_busy(pid, sample, now, baseline) for pid, sample in usage.items()
        )
        return self._last_busy

    def _sample(self, root_pid: int) -> dict[int, _Usage]:
        try:
            descendants = self._process(root_pid).children(recursive=True)
        except (psutil.Error, OSError):
            return {}
        usage: dict[int, _Usage] = {}
        for proc in descendants:
            try:
                times = proc.cpu_times()
                created_at = float(proc.create_time())
            except (psutil.Error, OSError):
                continue
            if isinstance(times, _CpuTimes):
                usage[proc.pid] = _Usage(float(times.user) + float(times.system), created_at)
        return usage

    def _ratio_at_least(self, used: float, span: float) -> bool:
        return span > 0 and used / span >= self._cpu_ratio

    def _is_busy(
        self,
        pid: int,
        sample: _Usage,
        now: float,
        baseline: tuple[float, dict[int, _Usage]] | None,
    ) -> bool:
        windowed = False
        if baseline is not None and pid in baseline[1]:
            span = now - baseline[0]
            windowed = self._ratio_at_least(sample.cpu_seconds - baseline[1][pid].cpu_seconds, span)
            if span >= self._min_span_seconds:
                return windowed
        lifetime = self._ratio_at_least(sample.cpu_seconds, now - sample.created_at)
        return windowed or lifetime


def quiet_state_with_busy_descendants(
    reported: AgentExecutionState, busy: Callable[[], bool]
) -> AgentExecutionState:
    if reported is AgentExecutionState.ACTIVE and busy():
        return AgentExecutionState.WAITING_ON_CHILD
    return reported


def alive_by_with_busy_descendants(
    alive_by: AliveBy | None, busy: Callable[[], bool]
) -> AliveBy | None:
    if alive_by in _UPGRADABLE_ALIVE_BY and busy():
        return AliveBy.OS_DESCENDANT_BUSY
    return alive_by


__all__ = [
    "MIN_MEASURED_SPAN_SECONDS",
    "BusyDescendantProbe",
    "alive_by_with_busy_descendants",
    "quiet_state_with_busy_descendants",
]
