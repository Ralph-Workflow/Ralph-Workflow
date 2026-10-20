"""Comprehensive tests for TreeSampler and typed adapter (§3b / WU-2).

All tests use deterministic fake clocks and mock process trees — no wall-clock sleeps,
no real subprocesses, fully typed with no type: ignore.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from ralph.process.manager._liveness_assessor import LivenessConfig
from ralph.process.manager._liveness_sampler import (
    PsutilModuleAdapter,
    PsutilProcessAdapter,
    TreeSampler,
    _LivenessProc,
)


class _CpuTimesNamedTuple(NamedTuple):
    user: float
    system: float


CpuTimesNamedTuple = _CpuTimesNamedTuple


class _IoCountersNamedTuple(NamedTuple):
    read_bytes: int
    write_bytes: int


IoCountersNamedTuple = _IoCountersNamedTuple


class _CtxSwitchesNamedTuple(NamedTuple):
    voluntary: int
    involuntary: int = 0


CtxSwitchesNamedTuple = _CtxSwitchesNamedTuple


@dataclass
class _FakeProcess:
    pid: int
    user_cpu: float = 0.0
    system_cpu: float = 0.0
    read_bytes: int = 0
    write_bytes: int = 0
    voluntary_ctx: int = 0
    involuntary_ctx: int = 0
    creation_time: float = 0.0
    child_procs: list[FakeProcess] = field(default_factory=list)

    raise_cpu: bool = False
    raise_io: bool = False
    raise_ctx: bool = False
    raise_children: bool = False
    raise_create_time: bool = False

    def children(self, recursive: bool = False) -> Sequence[_LivenessProc]:
        if self.raise_children:
            raise PermissionError("Access denied to children")
        if not recursive:
            return list(self.child_procs)
        all_descendants: list[_LivenessProc] = []
        for child in self.child_procs:
            all_descendants.append(child)
            all_descendants.extend(child.children(recursive=True))
        return all_descendants

    def cpu_times(self) -> CpuTimesNamedTuple:
        if self.raise_cpu:
            raise PermissionError("Access denied to cpu_times")
        return CpuTimesNamedTuple(user=self.user_cpu, system=self.system_cpu)

    def io_counters(self) -> IoCountersNamedTuple:
        if self.raise_io:
            raise OSError("io_counters not supported on this platform")
        return IoCountersNamedTuple(read_bytes=self.read_bytes, write_bytes=self.write_bytes)

    def num_ctx_switches(self) -> CtxSwitchesNamedTuple:
        if self.raise_ctx:
            raise PermissionError("Access denied to num_ctx_switches")
        return CtxSwitchesNamedTuple(voluntary=self.voluntary_ctx, involuntary=self.involuntary_ctx)

    def create_time(self) -> float:
        if self.raise_create_time:
            raise OSError("Cannot read create_time")
        return self.creation_time


FakeProcess = _FakeProcess


class _FakePsutilModule:
    Process: Callable[[int], _LivenessProc]

    def __init__(self, processes: dict[int, _LivenessProc] | None = None) -> None:
        self.procs: dict[int, _LivenessProc] = processes or {}
        self.Process = self._get_process

    def _get_process(self, pid: int) -> _LivenessProc:
        if pid not in self.procs:
            raise ProcessLookupError(f"No process with pid {pid}")
        return self.procs[pid]


FakePsutilModule = _FakePsutilModule


def _make_config(
    evidence_window_s: float = 30.0,
    tree_active_threshold: int = 1,
) -> LivenessConfig:
    return LivenessConfig(
        output_recent_min_s=10.0,
        output_recent_max_s=60.0,
        cpu_busy_min=0.02,
        cpu_busy_max=0.20,
        io_kbps_inactive=4.0,
        io_kbps_active=64.0,
        tree_active_threshold=tree_active_threshold,
        repetitive_max=0.1,
        repetitive_min=0.5,
        awake_per_s_inactive=0.1,
        awake_per_s_active=2.0,
        stall_theta=0.5,
        evidence_window_s=evidence_window_s,
    )


class _FakeClock:
    def __init__(self, start_time: float = 100.0) -> None:
        self.current_time = start_time

    def __call__(self) -> float:
        return self.current_time

    def advance(self, delta_s: float) -> None:
        self.current_time += delta_s


FakeClock = _FakeClock


def test_first_sample_signals_none_for_deltas() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001, user_cpu=1.0, system_cpu=0.5, read_bytes=2048, creation_time=95.0)
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    window = sampler.sample(1001, last_output_at=98.0, output_lines=[])

    assert window.seconds_since_output == 2.0
    assert window.cpu_ratio is None
    assert window.io_bytes_per_s is None
    assert window.voluntary_ctx_per_s is None
    assert window.child_events == 0
    assert window.output_novelty is None
    assert window.elapsed_s == 5.0


def test_consecutive_sample_computes_deltas() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(
        pid=1001,
        user_cpu=1.0,
        system_cpu=0.0,
        read_bytes=1024,
        write_bytes=1024,
        voluntary_ctx=10,
        creation_time=90.0,
    )
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    # Sample 1 at t=100.0
    sampler.sample(1001, last_output_at=100.0, output_lines=[])

    # Advance clock and stats by 2.0s
    clock.advance(2.0)
    root.user_cpu += 1.0  # 1.0s CPU in 2.0s wall time -> 0.5 ratio
    root.read_bytes += 2048
    root.write_bytes += 2048  # 4096 bytes / 2.0s / 1024 = 2.0 KiB/s
    root.voluntary_ctx += 6  # 6 switches / 2.0s = 3.0 ctx/s

    window = sampler.sample(1001, last_output_at=101.0, output_lines=[])

    assert window.seconds_since_output == 1.0
    assert window.cpu_ratio is not None
    assert abs(window.cpu_ratio - 0.5) < 1e-6
    assert window.io_bytes_per_s is not None
    assert abs(window.io_bytes_per_s - 2.0) < 1e-6
    assert window.voluntary_ctx_per_s is not None
    assert abs(window.voluntary_ctx_per_s - 3.0) < 1e-6
    assert window.elapsed_s == 12.0


def test_cpu_ratio_capped_at_one() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001, user_cpu=0.0, system_cpu=0.0)
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    sampler.sample(1001, last_output_at=100.0, output_lines=[])

    # Advance clock by 1.0s, but 4 threads used 4.0s of CPU
    clock.advance(1.0)
    root.user_cpu = 4.0

    window = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert window.cpu_ratio == 1.0


def test_wall_time_delta_non_positive_returns_none() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001, user_cpu=1.0)
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    sampler.sample(1001, last_output_at=100.0, output_lines=[])

    # Clock does not advance (dt = 0.0)
    root.user_cpu += 0.5
    window = sampler.sample(1001, last_output_at=100.0, output_lines=[])

    assert window.cpu_ratio is None
    assert window.io_bytes_per_s is None
    assert window.voluntary_ctx_per_s is None


def test_output_novelty_gate() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001)
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    # 19 lines: below 20-line gate -> None
    lines_19 = [f"line {i}" for i in range(19)]
    w1 = sampler.sample(1001, last_output_at=100.0, output_lines=lines_19)
    assert w1.output_novelty is None

    # 20 identical lines: ratio = 1 / 20 = 0.05
    lines_20_dup = ["same line"] * 20
    w2 = sampler.sample(1001, last_output_at=100.0, output_lines=lines_20_dup)
    assert w2.output_novelty is not None
    assert abs(w2.output_novelty - 0.05) < 1e-6

    # 20 distinct lines: ratio = 20 / 20 = 1.0
    lines_20_unique = [f"unique {i}" for i in range(20)]
    w3 = sampler.sample(1001, last_output_at=100.0, output_lines=lines_20_unique)
    assert w3.output_novelty == 1.0


def test_seconds_since_output() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001)
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    # None when last_output_at is None
    w1 = sampler.sample(1001, last_output_at=None, output_lines=[])
    assert w1.seconds_since_output is None

    # Normal age
    w2 = sampler.sample(1001, last_output_at=92.5, output_lines=[])
    assert w2.seconds_since_output is not None
    assert abs(w2.seconds_since_output - 7.5) < 1e-6

    # Future timestamp clamped to 0.0
    w3 = sampler.sample(1001, last_output_at=105.0, output_lines=[])
    assert w3.seconds_since_output == 0.0


def test_child_events_tracked_across_evidence_window() -> None:
    clock = FakeClock(100.0)
    child1 = FakeProcess(pid=2001)
    root = FakeProcess(pid=1001, child_procs=[child1])
    psutil_mod = FakePsutilModule({1001: root, 2001: child1})
    config = _make_config(evidence_window_s=10.0)
    sampler = TreeSampler(psutil_mod, clock, config)

    # Initial sample with 1 existing child -> child_events = 1
    w1 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w1.child_events == 1

    # At t=102, spawn child2 -> +1 start event -> total 2 events in window
    clock.advance(2.0)
    child2 = FakeProcess(pid=2002)
    root.child_procs.append(child2)
    psutil_mod.procs[2002] = child2
    w2 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w2.child_events == 2

    # At t=104, child1 exits -> +1 exit event -> total 3 events in window
    clock.advance(2.0)
    root.child_procs.remove(child1)
    del psutil_mod.procs[2001]
    w3 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w3.child_events == 3

    # At t=112 (12s after t=100), the initial sample (at t=100) has expired (>10s window).
    # Events in window [102..112]: child2 spawn (at t=102) and child1 exit (at t=104) = 2 events
    clock.advance(8.0)
    w4 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w4.child_events == 2

    # At t=120, all earlier events have expired (>10s window) -> 0 child events
    clock.advance(8.0)
    w5 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w5.child_events == 0


def test_tree_aggregation_across_descendants() -> None:
    clock = FakeClock(100.0)
    grandchild = FakeProcess(pid=3001, user_cpu=0.2, read_bytes=500)
    child = FakeProcess(pid=2001, user_cpu=0.3, read_bytes=500, child_procs=[grandchild])
    root = FakeProcess(pid=1001, user_cpu=0.5, read_bytes=1048, child_procs=[child])
    psutil_mod = FakePsutilModule({1001: root, 2001: child, 3001: grandchild})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    sampler.sample(1001, last_output_at=100.0, output_lines=[])

    # Advance clock by 1.0s and increase CPU and I/O on root, child, and grandchild
    clock.advance(1.0)
    root.user_cpu += 0.1
    child.user_cpu += 0.2
    grandchild.user_cpu += 0.3  # total delta cpu = 0.6s / 1.0s = 0.6
    root.read_bytes += 1024  # 1024 bytes / 1.0s / 1024 = 1.0 KiB/s

    window = sampler.sample(1001, last_output_at=100.0, output_lines=[])

    assert window.cpu_ratio is not None
    assert abs(window.cpu_ratio - 0.6) < 1e-6
    assert window.io_bytes_per_s is not None
    assert abs(window.io_bytes_per_s - 1.0) < 1e-6


def test_fail_closed_individual_signals() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001, user_cpu=1.0, read_bytes=1024, voluntary_ctx=10)
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    sampler.sample(1001, last_output_at=100.0, output_lines=[])
    clock.advance(1.0)
    root.user_cpu += 0.5
    root.read_bytes += 1024
    root.voluntary_ctx += 5

    # Case 1: io_counters raises OSError -> io_bytes_per_s is None, CPU and ctx still work
    root.raise_io = True
    w1 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w1.io_bytes_per_s is None
    assert w1.cpu_ratio is not None
    assert abs(w1.cpu_ratio - 0.5) < 1e-6
    assert w1.voluntary_ctx_per_s is not None
    assert abs(w1.voluntary_ctx_per_s - 5.0) < 1e-6

    # Case 2: restore io, make cpu fail -> cpu_ratio is None, io works
    clock.advance(1.0)
    root.raise_io = False
    root.read_bytes += 2048
    root.raise_cpu = True
    w2 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w2.cpu_ratio is None
    # Note: io delta computed against its previous successful snapshot
    assert w2.io_bytes_per_s is not None

    # Case 3: children reading fails -> child_events is None
    root.raise_children = True
    w3 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w3.child_events is None


def test_missing_root_process_yields_none_for_all_tree_signals() -> None:
    clock = FakeClock(100.0)
    psutil_mod = FakePsutilModule({})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    window = sampler.sample(9999, last_output_at=95.0, output_lines=["line"] * 20)

    assert window.seconds_since_output == 5.0
    assert window.output_novelty == 0.05
    assert window.cpu_ratio is None
    assert window.io_bytes_per_s is None
    assert window.child_events is None
    assert window.voluntary_ctx_per_s is None
    assert window.elapsed_s == 0.0


def test_switching_root_pid_resets_history() -> None:
    clock = FakeClock(100.0)
    p1 = FakeProcess(pid=1001, user_cpu=1.0, creation_time=50.0)
    p2 = FakeProcess(pid=1002, user_cpu=2.0, creation_time=80.0)
    psutil_mod = FakePsutilModule({1001: p1, 1002: p2})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    w1 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w1.elapsed_s == 50.0

    # Switch to PID 1002: resets start time and previous deltas
    w2 = sampler.sample(1002, last_output_at=100.0, output_lines=[])
    assert w2.elapsed_s == 20.0
    assert w2.cpu_ratio is None  # first sample for new pid -> None


def test_adapter_classes_wrap_process_and_module() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001, user_cpu=1.0, read_bytes=1024, voluntary_ctx=5)
    psutil_mod = FakePsutilModule({1001: root})

    # Wrap with adapters
    wrapped_module = PsutilModuleAdapter(psutil_mod)
    proc_adapter = wrapped_module.Process(1001)

    assert isinstance(proc_adapter, PsutilProcessAdapter)
    assert proc_adapter.pid == 1001
    assert proc_adapter.create_time() == 0.0
    assert len(proc_adapter.children()) == 0

    sampler = TreeSampler(wrapped_module, clock, _make_config())
    w = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w.seconds_since_output == 0.0


class _AltProcess:
    """Process returning stats as tuples/dicts instead of namedtuples."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.cpu: tuple[float, float] = (1.0, 0.5)
        self.io: tuple[int, int, int, int] = (10, 10, 2048, 2048)  # 4-tuple
        self.ctx: dict[str, int] = {"voluntary": 12, "involuntary": 4}

    def children(self, recursive: bool = False) -> Sequence[_LivenessProc]:
        return []

    def cpu_times(self) -> object:
        return self.cpu

    def io_counters(self) -> object:
        return self.io

    def num_ctx_switches(self) -> object:
        return self.ctx

    def create_time(self) -> float:
        return 90.0


AltProcess = _AltProcess


def test_different_stats_shapes() -> None:
    clock = FakeClock(100.0)
    proc = AltProcess(pid=1001)
    psutil_mod = FakePsutilModule({1001: proc})
    # Note: AltProcess implements _LivenessProc structural protocol
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    sampler.sample(1001, last_output_at=100.0, output_lines=[])

    clock.advance(2.0)
    proc.cpu = (2.0, 1.5)  # delta = (2+1.5) - (1+0.5) = 2.0s / 2s = 1.0
    proc.io = (12, 12, 4096, 4096)  # delta = 8192 - 4096 = 4096B / 2s / 1024 = 2.0 KiB/s
    proc.ctx = {"voluntary": 20, "involuntary": 4}  # delta = 8 / 2s = 4.0 ctx/s

    window = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert window.cpu_ratio == 1.0
    assert window.io_bytes_per_s is not None
    assert abs(window.io_bytes_per_s - 2.0) < 1e-6
    assert window.voluntary_ctx_per_s is not None
    assert abs(window.voluntary_ctx_per_s - 4.0) < 1e-6


class _NoRecursiveProcess:
    """Process whose children() does not accept recursive=True at runtime."""

    def __init__(self, pid: int, children_list: list[_LivenessProc]) -> None:
        self.pid = pid
        self._children = children_list

    def children(self, recursive: bool = False) -> Sequence[_LivenessProc]:
        if recursive:
            raise TypeError("recursive=True not supported by this mock")
        return self._children

    def cpu_times(self) -> tuple[float, float]:
        return (0.1, 0.1)

    def io_counters(self) -> tuple[int, int]:
        return (100, 100)

    def num_ctx_switches(self) -> tuple[int]:
        return (5,)

    def create_time(self) -> float:
        return 90.0


NoRecursiveProcess = _NoRecursiveProcess


def test_children_recursive_type_error_falls_back_to_bfs() -> None:
    clock = FakeClock(100.0)
    grandchild = NoRecursiveProcess(pid=3001, children_list=[])
    child = NoRecursiveProcess(pid=2001, children_list=[grandchild])
    root = NoRecursiveProcess(pid=1001, children_list=[child])

    psutil_mod = FakePsutilModule({1001: root, 2001: child, 3001: grandchild})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    window = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert window.child_events == 2  # child and grandchild found via BFS


def test_long_delay_exceeding_evidence_window_resets_baseline() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001, user_cpu=1.0)
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config(evidence_window_s=30.0))

    sampler.sample(1001, last_output_at=100.0, output_lines=[])

    # Advance clock by 35.0s (> 30.0s evidence window)
    clock.advance(35.0)
    root.user_cpu += 10.0
    w1 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w1.cpu_ratio is None  # expired window resets baseline

    # Next sample at 2.0s later should succeed
    clock.advance(2.0)
    root.user_cpu += 1.0  # 1.0s / 2.0s = 0.5
    w2 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert w2.cpu_ratio is not None
    assert abs(w2.cpu_ratio - 0.5) < 1e-6


def test_backwards_clock_returns_none() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001, user_cpu=1.0)
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    sampler.sample(1001, last_output_at=100.0, output_lines=[])

    # Clock moves backwards
    clock.advance(-5.0)
    window = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert window.cpu_ratio is None
    assert window.io_bytes_per_s is None
    assert window.voluntary_ctx_per_s is None


def test_root_create_time_in_future_falls_back_to_clock() -> None:
    clock = FakeClock(100.0)
    root = FakeProcess(pid=1001, creation_time=200.0)  # Future creation time
    psutil_mod = FakePsutilModule({1001: root})
    sampler = TreeSampler(psutil_mod, clock, _make_config())

    window = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert window.elapsed_s == 0.0

    clock.advance(3.0)
    window2 = sampler.sample(1001, last_output_at=100.0, output_lines=[])
    assert window2.elapsed_s == 3.0


def test_assessor_integration_with_sampled_window() -> None:
    from ralph.process.manager._liveness_assessor import assess

    clock = FakeClock(100.0)
    root = FakeProcess(
        pid=1001,
        user_cpu=1.0,
        read_bytes=100000,
        voluntary_ctx=10,
        creation_time=90.0,
    )
    psutil_mod = FakePsutilModule({1001: root})
    config = _make_config()
    sampler = TreeSampler(psutil_mod, clock, config)

    # First sample
    sampler.sample(1001, last_output_at=100.0, output_lines=[])

    # Advance clock: active output, active CPU, active IO
    clock.advance(2.0)
    root.user_cpu += 0.5  # 0.5 / 2.0 = 0.25 (busy)
    root.read_bytes += 200000  # ~97 KiB/s (active)
    output_lines = [f"compiling {i}.o" for i in range(25)]

    window = sampler.sample(1001, last_output_at=102.0, output_lines=output_lines)
    verdict = assess(window, config)

    assert verdict.verdict_kind == "working"
    assert verdict.score > 0.8
    assert verdict.suspicious is False
