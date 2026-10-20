"""Liveness tree sampler for long-running exec jobs (§3b).

This module samples activity across an execution process tree (CPU ratio,
I/O throughput, child process events, output novelty, and context switches)
and produces a :class:`LivenessWindow` consumable by
:func:`ralph.process.manager._liveness_assessor.assess`.

All metrics follow fail-closed error handling: any signal that cannot be read
on the root process or any child process defaults to ``None`` individually.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.process.manager._liveness_assessor import LivenessConfig, LivenessWindow

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_MIN_OUTPUT_LINES_FOR_NOVELTY: int = 20
_MIN_TUPLE_LEN_TWO: int = 2
_MIN_TUPLE_LEN_FOUR: int = 4
_KIBIBYTE: float = 1024.0


@runtime_checkable
class _LivenessProc(Protocol):
    """Sampler-local typed adapter protocol for process tree nodes (§3b)."""

    @property
    def pid(self) -> int: ...

    def children(self, recursive: bool = False) -> Sequence[_LivenessProc]: ...

    def cpu_times(self) -> object: ...  # .user, .system or tuple

    def io_counters(self) -> object: ...  # .read_bytes, .write_bytes

    def num_ctx_switches(self) -> object: ...  # .voluntary

    def create_time(self) -> float: ...


@runtime_checkable
class _CpuTimesObj(Protocol):
    user: float
    system: float


@runtime_checkable
class _IoCountersObj(Protocol):
    read_bytes: int | float
    write_bytes: int | float


@runtime_checkable
class _CtxSwitchesObj(Protocol):
    voluntary: int | float


class _PsutilModuleLike(Protocol):
    """Protocol for psutil module or equivalent factory."""

    Process: Callable[[int], _LivenessProc]


class _PsutilProcessAdapter:
    """Typed adapter wrapping a psutil-compatible process."""

    def __init__(self, proc: LivenessProc) -> None:
        self._proc = proc

    @property
    def pid(self) -> int:
        return self._proc.pid

    def children(self, recursive: bool = False) -> Sequence[LivenessProc]:
        try:
            raw_children = self._proc.children(recursive=recursive)
        except TypeError:
            raw_children = self._proc.children()
        return [_PsutilProcessAdapter(c) for c in raw_children]

    def cpu_times(self) -> object:
        return self._proc.cpu_times()

    def io_counters(self) -> object:
        return self._proc.io_counters()

    def num_ctx_switches(self) -> object:
        return self._proc.num_ctx_switches()

    def create_time(self) -> float:
        return self._proc.create_time()


class _PsutilModuleAdapter:
    """Typed adapter wrapping a psutil module."""

    def __init__(self, psutil_mod: _PsutilModuleLike) -> None:
        self._psutil = psutil_mod
        self.Process: Callable[[int], LivenessProc] = self._get_process

    def _get_process(self, pid: int) -> LivenessProc:
        return _PsutilProcessAdapter(self._psutil.Process(pid))


PsutilProcessAdapter = _PsutilProcessAdapter
PsutilModuleAdapter = _PsutilModuleAdapter
LivenessProc = _LivenessProc


def _extract_cpu_seconds(val: object) -> float:
    if isinstance(val, _CpuTimesObj):
        return float(val.user) + float(val.system)
    if isinstance(val, (tuple, list)) and len(val) >= _MIN_TUPLE_LEN_TWO:
        return float(val[0]) + float(val[1])
    if isinstance(val, (int, float)):
        return float(val)
    raise TypeError(f"Unrecognized cpu_times shape: {type(val)!r}")


def _extract_io_bytes(val: object) -> float:
    if isinstance(val, _IoCountersObj):
        return float(val.read_bytes) + float(val.write_bytes)
    if isinstance(val, dict):
        return float(val.get("read_bytes", 0.0)) + float(val.get("write_bytes", 0.0))
    if isinstance(val, (tuple, list)) and len(val) >= _MIN_TUPLE_LEN_FOUR:
        return float(val[2]) + float(val[3])
    if isinstance(val, (tuple, list)) and len(val) >= _MIN_TUPLE_LEN_TWO:
        return float(val[0]) + float(val[1])
    if isinstance(val, (int, float)):
        return float(val)
    raise TypeError(f"Unrecognized io_counters shape: {type(val)!r}")


def _extract_voluntary_ctx(val: object) -> float:
    if isinstance(val, _CtxSwitchesObj):
        return float(val.voluntary)
    if isinstance(val, dict):
        return float(val.get("voluntary", 0.0))
    if isinstance(val, (tuple, list)) and len(val) >= 1:
        return float(val[0])
    if isinstance(val, (int, float)):
        return float(val)
    raise TypeError(f"Unrecognized num_ctx_switches shape: {type(val)!r}")


def _collect_tree(
    root: _LivenessProc,
) -> tuple[list[_LivenessProc], set[int] | None]:
    """Collect all reachable processes in the tree rooted at root.

    Returns:
        (all_processes, child_pids). If reading children fails, child_pids is None.
    """
    try:
        children_seq = root.children(recursive=True)
        children = list(children_seq)
        child_pids: set[int] = {c.pid for c in children}
        return [root, *children], child_pids
    except TypeError:
        # Fallback if recursive kwarg is unsupported
        pass
    except Exception:
        return [root], None

    try:
        procs: list[_LivenessProc] = [root]
        child_pids_set: set[int] = set()
        queue: list[_LivenessProc] = [root]
        while queue:
            curr = queue.pop(0)
            for c in curr.children():
                if c.pid not in child_pids_set and c.pid != root.pid:
                    child_pids_set.add(c.pid)
                    procs.append(c)
                    queue.append(c)
        return procs, child_pids_set
    except Exception:
        return [root], None


def _compute_output_novelty(output_lines: Sequence[str]) -> float | None:
    if len(output_lines) < _MIN_OUTPUT_LINES_FOR_NOVELTY:
        return None
    return len(set(output_lines)) / float(len(output_lines))


class TreeSampler:
    """Samples process tree activity and produces a :class:`LivenessWindow`."""

    def __init__(
        self,
        psutil_module: _PsutilModuleLike,
        clock: Callable[[], float],
        config: LivenessConfig,
    ) -> None:
        self._psutil_module = psutil_module
        self._clock = clock
        self._config = config

        self._tracked_root_pid: int | None = None
        self._start_time: float | None = None

        self._prev_child_pids: set[int] | None = None
        self._child_events_history: deque[tuple[float, int]] = deque(
            maxlen=120
        )  # bounded-accumulator-ok: FIFO queue capped at 120 window entries

        self._prev_cpu_snapshot: tuple[float, dict[int, float]] | None = None
        self._prev_io_snapshot: tuple[float, dict[int, float]] | None = None
        self._prev_ctx_snapshot: tuple[float, dict[int, float]] | None = None

    def _reset_tracked_pid_state(self, root_pid: int) -> None:
        self._tracked_root_pid = root_pid
        self._start_time = None
        self._prev_child_pids = None
        self._child_events_history.clear()
        self._prev_cpu_snapshot = None
        self._prev_io_snapshot = None
        self._prev_ctx_snapshot = None

    def _determine_start_time(self, root: _LivenessProc | None, now: float) -> None:
        if self._start_time is not None:
            return
        tree_created: float | None = None
        if root is not None:
            try:
                c_time = float(root.create_time())
                if 0.0 <= c_time <= now:
                    tree_created = c_time
            except Exception:
                tree_created = None
        if tree_created is not None:
            self._start_time = tree_created
        else:
            self._start_time = now

    def _sample_child_events(
        self,
        child_pids: set[int] | None,
        now: float,
    ) -> int | None:
        if child_pids is None:
            self._prev_child_pids = None
            return None

        if self._prev_child_pids is None:
            started = child_pids
            exited: set[int] = set()
        else:
            started = child_pids - self._prev_child_pids
            exited = self._prev_child_pids - child_pids
        events_count = len(started) + len(exited)
        self._prev_child_pids = child_pids
        self._child_events_history.append((now, events_count))

        cutoff = now - self._config.evidence_window_s
        return sum(cnt for ts, cnt in self._child_events_history if ts >= cutoff)

    def _sample_cpu_ratio(
        self,
        procs: list[_LivenessProc],
        now: float,
    ) -> float | None:
        curr_cpu_by_pid: dict[int, float] = {}
        for p in procs:
            try:
                raw_cpu = p.cpu_times()
                curr_cpu_by_pid[p.pid] = _extract_cpu_seconds(raw_cpu)
            except Exception:
                return None

        if self._prev_cpu_snapshot is None:
            self._prev_cpu_snapshot = (now, curr_cpu_by_pid)
            return None

        prev_time, prev_cpu = self._prev_cpu_snapshot
        self._prev_cpu_snapshot = (now, curr_cpu_by_pid)
        dt = now - prev_time
        if dt <= 0.0 or dt > self._config.evidence_window_s:
            return None

        delta_cpu = 0.0
        for pid, curr_c in curr_cpu_by_pid.items():
            if pid in prev_cpu:
                delta_cpu += max(0.0, curr_c - prev_cpu[pid])
            else:
                delta_cpu += max(0.0, curr_c)
        return min(1.0, max(0.0, delta_cpu / dt))

    def _sample_io_bytes_per_s(
        self,
        procs: list[_LivenessProc],
        now: float,
    ) -> float | None:
        curr_io_by_pid: dict[int, float] = {}
        for p in procs:
            try:
                raw_io = p.io_counters()
                curr_io_by_pid[p.pid] = _extract_io_bytes(raw_io)
            except Exception:
                return None

        if self._prev_io_snapshot is None:
            self._prev_io_snapshot = (now, curr_io_by_pid)
            return None

        prev_time, prev_io = self._prev_io_snapshot
        self._prev_io_snapshot = (now, curr_io_by_pid)
        dt = now - prev_time
        if dt <= 0.0 or dt > self._config.evidence_window_s:
            return None

        delta_io = 0.0
        for pid, curr_b in curr_io_by_pid.items():
            if pid in prev_io:
                delta_io += max(0.0, curr_b - prev_io[pid])
            else:
                delta_io += max(0.0, curr_b)
        return max(0.0, (delta_io / dt) / _KIBIBYTE)

    def _sample_voluntary_ctx(
        self,
        procs: list[_LivenessProc],
        now: float,
    ) -> float | None:
        curr_ctx_by_pid: dict[int, float] = {}
        for p in procs:
            try:
                raw_ctx = p.num_ctx_switches()
                curr_ctx_by_pid[p.pid] = _extract_voluntary_ctx(raw_ctx)
            except Exception:
                return None

        if self._prev_ctx_snapshot is None:
            self._prev_ctx_snapshot = (now, curr_ctx_by_pid)
            return None

        prev_time, prev_ctx = self._prev_ctx_snapshot
        self._prev_ctx_snapshot = (now, curr_ctx_by_pid)
        dt = now - prev_time
        if dt <= 0.0 or dt > self._config.evidence_window_s:
            return None

        delta_ctx = 0.0
        for pid, curr_sw in curr_ctx_by_pid.items():
            if pid in prev_ctx:
                delta_ctx += max(0.0, curr_sw - prev_ctx[pid])
            else:
                delta_ctx += max(0.0, curr_sw)
        return max(0.0, delta_ctx / dt)

    def sample(
        self,
        root_pid: int,
        last_output_at: float | None,
        output_lines: Sequence[str],
    ) -> LivenessWindow:
        now = float(self._clock())

        if self._tracked_root_pid != root_pid:
            self._reset_tracked_pid_state(root_pid)

        seconds_since_output: float | None
        if last_output_at is not None:
            seconds_since_output = max(0.0, now - float(last_output_at))
        else:
            seconds_since_output = None

        output_novelty = _compute_output_novelty(output_lines)

        try:
            root: _LivenessProc | None = self._psutil_module.Process(root_pid)
        except Exception:
            root = None

        self._determine_start_time(root, now)
        elapsed_s = max(0.0, now - (self._start_time or now))

        if root is None:
            self._prev_child_pids = None
            self._prev_cpu_snapshot = None
            self._prev_io_snapshot = None
            self._prev_ctx_snapshot = None
            return LivenessWindow(
                seconds_since_output=seconds_since_output,
                cpu_ratio=None,
                io_bytes_per_s=None,
                child_events=None,
                output_novelty=output_novelty,
                voluntary_ctx_per_s=None,
                elapsed_s=elapsed_s,
            )

        procs, child_pids = _collect_tree(root)

        return LivenessWindow(
            seconds_since_output=seconds_since_output,
            cpu_ratio=self._sample_cpu_ratio(procs, now),
            io_bytes_per_s=self._sample_io_bytes_per_s(procs, now),
            child_events=self._sample_child_events(child_pids, now),
            output_novelty=output_novelty,
            voluntary_ctx_per_s=self._sample_voluntary_ctx(procs, now),
            elapsed_s=elapsed_s,
        )


__all__ = [
    "PsutilModuleAdapter",
    "PsutilProcessAdapter",
    "TreeSampler",
    "_LivenessProc",
    "_PsutilModuleLike",
]
