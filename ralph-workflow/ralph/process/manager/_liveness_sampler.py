"""Liveness tree sampler for long-running exec jobs (§3b).

This module samples activity across an execution process tree (CPU ratio,
I/O throughput, child process events, output novelty, and context switches)
and produces a :class:`LivenessWindow` consumable by
:func:`ralph.process.manager._liveness_assessor.assess`.

All metrics follow fail-closed error handling: any signal that cannot be read
on the root process or any child process defaults to ``None`` individually.

Per-rate signals (``cpu_ratio``, ``io_bytes_per_s``, ``voluntary_ctx_per_s``)
are aggregated over the bounded
``LivenessConfig.evidence_window_s`` window (default 30 s) rather than
over the last sample interval. The sampler keeps a per-signal deque of
``(timestamp, cumulative_total)`` snapshots, prunes entries older than the
window, and computes ``(current - oldest_in_window) / window_dt`` so a
slow steady drip of work reads as a small positive rate even when each
2 s sample advances the cumulative counters only slightly. A gap larger
than ``evidence_window_s`` resets the baseline (returning ``None`` for one
cycle) per the failure-recovery policy in D3.

Output novelty (``output_novelty``) obeys the SAME evidence-window rule.
The producer hands over timestamped :class:`OutputEvidence` records, and
:meth:`TreeSampler.sample` prunes every record older than
``now - evidence_window_s`` before taking the distinct/total ratio. Without
that pruning a historic burst of distinct startup lines keeps novelty pinned
high forever, so a command that has since degenerated into printing one
line per second reads as permanently novel, ``R6`` (Repetitive) never
fires, stall pressure never builds, and the job is never killed as
``stalled``. Records are a caller-owned bounded FIFO; the sampler holds no
accumulator of its own for them.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

from ralph.process.manager._liveness_assessor import LivenessConfig, LivenessWindow

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_MIN_OUTPUT_LINES_FOR_NOVELTY: int = 20
_MIN_TUPLE_LEN_TWO: int = 2
_MIN_TUPLE_LEN_FOUR: int = 4
_KIBIBYTE: float = 1024.0
_HISTORY_MAXLEN: int = 256  # bounded-accumulator-ok: 30 s window @ 0.125 s sampler → 240 entries max; 256 leaves headroom


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


class _OutputEvidence(NamedTuple):
    """One decoded output line plus the instant the reader observed it.

    The timestamp is the producer's own clock reading at the moment the
    line was appended to the job's bounded output FIFO (the same reading
    that seeds ``last_output_at``), so novelty can be scoped to the
    evidence window instead of to the FIFO's whole retention.
    """

    emitted_at: float
    line: str


OutputEvidence = _OutputEvidence


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


def _compute_output_novelty(
    samples: Sequence[OutputEvidence],
    now: float,
    evidence_window_s: float,
) -> float | None:
    """Distinct/total line ratio measured over the evidence window only.

    Records observed before ``now - evidence_window_s`` are dropped first,
    so novelty describes what the command printed *now* rather than what
    it printed at any point in the FIFO's retention. The gate is unchanged:
    fewer than ``_MIN_OUTPUT_LINES_FOR_NOVELTY`` records inside the window
    leaves novelty unknown (``None``), which the assessor treats as "no
    repetitiveness evidence" per D5.
    """
    cutoff = now - evidence_window_s
    in_window = [sample.line for sample in samples if sample.emitted_at >= cutoff]
    if len(in_window) < _MIN_OUTPUT_LINES_FOR_NOVELTY:
        return None
    return len(set(in_window)) / float(len(in_window))


def _windowed_rate(
    history: deque[tuple[float, float]],
    now: float,
    current_total: float,
    evidence_window_s: float,
    *,
    cap_to_unit_interval: bool,
) -> float | None:
    """Aggregate a per-second rate across the bounded evidence window.

    For every call we (a) prune entries older than ``now - evidence_window_s``,
    (b) record the current ``(now, current_total)`` snapshot, (c) compute
    the rate as ``(current_total - oldest_in_window) / window_dt``. The
    first sample in a window — or any sample following a gap wider than
    the window — returns ``None`` so the caller can re-baseline without
    contaminating the rate with stale deltas.

    ``cap_to_unit_interval`` controls the output range: ``True`` clamps the
    rate to ``[0, 1]`` (used for cpu ratio), ``False`` clamps to ``[0, +inf)``
    (used for I/O bytes/s and voluntary context switches/s).
    """
    cutoff = now - evidence_window_s
    while history and history[0][0] < cutoff:
        history.popleft()
    history.append((now, current_total))
    min_samples = 2
    if len(history) < min_samples:
        return None
    oldest_ts, oldest_total = history[0]
    dt = now - oldest_ts
    if dt <= 0.0:
        return None
    delta = max(0.0, current_total - oldest_total)
    rate = delta / dt
    if cap_to_unit_interval:
        return min(1.0, max(0.0, rate))
    return max(0.0, rate)


def _sum_cpu_seconds(procs: list[_LivenessProc]) -> tuple[float | None, bool]:
    """Return ``(sum_cpu_seconds, success_flag)``.

    ``success_flag=False`` indicates that the cumulative counter could not
    be read on at least one process; the caller must treat the whole
    signal as unavailable and not pollute the rate history.
    """
    total = 0.0
    for p in procs:
        try:
            total += _extract_cpu_seconds(p.cpu_times())
        except Exception:
            return None, False
    return total, True


def _sum_io_bytes(procs: list[_LivenessProc]) -> tuple[float | None, bool]:
    total = 0.0
    for p in procs:
        try:
            total += _extract_io_bytes(p.io_counters())
        except Exception:
            return None, False
    return total, True


def _sum_voluntary_ctx(procs: list[_LivenessProc]) -> tuple[float | None, bool]:
    total = 0.0
    for p in procs:
        try:
            total += _extract_voluntary_ctx(p.num_ctx_switches())
        except Exception:
            return None, False
    return total, True


class TreeSampler:
    """Samples process tree activity and produces a :class:`LivenessWindow`.

    Per-rate signals are aggregated across ``config.evidence_window_s``
    (default ``EXEC_LIVENESS_EVIDENCE_WINDOW_S`` = 30.0 s) rather than the
    last sample interval. ``child_events`` keeps the same windowed
    semantics as before, and ``output_novelty`` is measured over the same
    window from the timestamped ``output_samples`` the producer supplies.
    An unreadable signal returns ``None`` for one
    cycle and leaves the per-signal windowed history untouched.
    """

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
            maxlen=_HISTORY_MAXLEN
        )  # bounded-accumulator-ok: FIFO windowed accumulator; 256-entry safety cap

        self._cpu_history: deque[tuple[float, float]] = deque(
            maxlen=_HISTORY_MAXLEN
        )  # bounded-accumulator-ok: 30 s evidence window @ 0.125 s sampler → ≤240 entries; 256 leaves headroom
        self._io_history: deque[tuple[float, float]] = deque(
            maxlen=_HISTORY_MAXLEN
        )  # bounded-accumulator-ok: same windowed FIFO accumulator contract
        self._ctx_history: deque[tuple[float, float]] = deque(
            maxlen=_HISTORY_MAXLEN
        )  # bounded-accumulator-ok: same windowed FIFO accumulator contract

    def _reset_tracked_pid_state(self, root_pid: int) -> None:
        self._tracked_root_pid = root_pid
        self._start_time = None
        self._prev_child_pids = None
        self._child_events_history.clear()
        self._cpu_history.clear()
        self._io_history.clear()
        self._ctx_history.clear()

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
        total, ok = _sum_cpu_seconds(procs)
        if not ok or total is None:
            return None
        return _windowed_rate(
            self._cpu_history,
            now,
            total,
            self._config.evidence_window_s,
            cap_to_unit_interval=True,
        )

    def _sample_io_bytes_per_s(
        self,
        procs: list[_LivenessProc],
        now: float,
    ) -> float | None:
        total, ok = _sum_io_bytes(procs)
        if not ok or total is None:
            return None
        rate = _windowed_rate(
            self._io_history,
            now,
            total,
            self._config.evidence_window_s,
            cap_to_unit_interval=False,
        )
        if rate is None:
            return None
        return rate / _KIBIBYTE

    def _sample_voluntary_ctx(
        self,
        procs: list[_LivenessProc],
        now: float,
    ) -> float | None:
        total, ok = _sum_voluntary_ctx(procs)
        if not ok or total is None:
            return None
        return _windowed_rate(
            self._ctx_history,
            now,
            total,
            self._config.evidence_window_s,
            cap_to_unit_interval=False,
        )

    def sample(
        self,
        root_pid: int,
        last_output_at: float | None,
        output_samples: Sequence[OutputEvidence],
    ) -> LivenessWindow:
        now = float(self._clock())

        if self._tracked_root_pid != root_pid:
            self._reset_tracked_pid_state(root_pid)

        seconds_since_output: float | None
        if last_output_at is not None:
            seconds_since_output = max(0.0, now - float(last_output_at))
        else:
            seconds_since_output = None

        output_novelty = _compute_output_novelty(
            output_samples, now, self._config.evidence_window_s
        )

        try:
            root: _LivenessProc | None = self._psutil_module.Process(root_pid)
        except Exception:
            root = None

        self._determine_start_time(root, now)
        elapsed_s = max(0.0, now - (self._start_time or now))

        if root is None:
            self._prev_child_pids = None
            self._cpu_history.clear()
            self._io_history.clear()
            self._ctx_history.clear()
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
    "OutputEvidence",
    "PsutilModuleAdapter",
    "PsutilProcessAdapter",
    "TreeSampler",
    "_LivenessProc",
    "_PsutilModuleLike",
]
