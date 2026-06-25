"""Black-box tests for the HARD-FIFO-bounded subagent output capture cache.

wt-024 Step 6 (AC-04): ``_subagent_output_captures`` in
``IdleWatchdog`` is an ``OrderedDict`` capped at
``_MAX_SUBAGENT_OUTPUT_CAPTURES = 128`` (defined in
``_activity_methods``). Inserting more than the cap evicts the
least-recently-used (LRU) entry so a single high-fan-out
watchdog tick that sees many distinct worker IDs cannot grow the
dict unboundedly within one invocation.

The cap is a HARD bound: when the cap binds, the LRU worker is
evicted regardless of whether it is still live or not. To
preserve the no-duplicate-output property of stateful
``SubagentOutputCapture`` implementations (the production
``FileSubagentOutputCapture`` tracks a per-worker byte offset
and would otherwise re-read historical lines if recreated from
offset 0 after eviction), evicted worker IDs are recorded in a
bounded ``_evicted_worker_tombstones`` map. Tombstoned workers
are skipped on the next poll so they cannot immediately re-enter
the cache and re-emit historical lines. The tombstone is itself
bounded at ``_MAX_EVICTED_TOMBSTONES = 128`` using FIFO eviction.

The tests drive the production PUBLIC entry point
:meth:`IdleWatchdog.poll_subagent_output` with a fake
``ProcessMonitor`` whose ``discover_subagent_outputs`` returns a
deterministic mapping of fresh worker_ids to fresh
``SubagentOutputCapture`` instances on every call. No direct
``OrderedDict`` mutation, no direct ``_subagent_output_captures``
assignment, no real subprocess, no real file I/O, no
``time.sleep``. ``FakeClock`` drives the watchdog's clock
deterministically.

Tests use ``monkeypatch.setattr`` to override the module-level
constants ``_MAX_SUBAGENT_OUTPUT_CAPTURES`` and
``_MAX_EVICTED_TOMBSTONES`` so the hard cap actually binds at a
small value; the production cap (128) would otherwise be too
large to exercise in a unit test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
)
from ralph.agents.idle_watchdog import _activity_methods as _activity_methods_module
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pytest

    from ralph.process.monitor._subagent_output_capture import SubagentOutputCapture


class _StaticCapture:
    """A SubagentOutputCapture that returns one line per ``read_lines`` call."""

    def __init__(self) -> None:
        self.read_count = 0

    def read_lines(self, worker_id: str) -> list[str]:
        self.read_count += 1
        return [f"line-for-{worker_id}"]


class _StaticCaptureEmpty:
    """A SubagentOutputCapture that returns no lines (so poll_subagent_output
    is a no-op for count).
    """

    def read_lines(self, worker_id: str) -> list[str]:
        del worker_id
        return []


class _FakeProcessMonitor:
    """ProcessMonitor whose ``discover_subagent_outputs`` is callable-driven."""

    def __init__(self, captures: Mapping[str, SubagentOutputCapture]) -> None:
        self._captures = dict(captures)
        self.discover_calls = 0

    def replace_captures(self, captures: Mapping[str, SubagentOutputCapture]) -> None:
        """Atomically swap the active set of workers (simulates a churn)."""
        self._captures = dict(captures)

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        self.discover_calls += 1
        return dict(self._captures)

    def live_subagent_count(self) -> int:
        return len(self._captures)

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass


def _make_watchdog(
    monitor: _FakeProcessMonitor,
    cap: int,
    tombstone_cap: int,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog with the cap and tombstone overridden to ``cap``/``tombstone_cap``.

    The production module-level constants ``_MAX_SUBAGENT_OUTPUT_CAPTURES``
    (128) and ``_MAX_EVICTED_TOMBSTONES`` (128) are too large to exercise
    in a unit test, so we monkeypatch them down to a small value (typically
    8) before constructing the watchdog. The watchdog reads the constants
    at every ``poll_subagent_output`` call, so the monkeypatch must be in
    place for the entire test.
    """
    monkeypatch.setattr(
        _activity_methods_module,
        "_MAX_SUBAGENT_OUTPUT_CAPTURES",
        cap,
    )
    monkeypatch.setattr(
        _activity_methods_module,
        "_MAX_EVICTED_TOMBSTONES",
        tombstone_cap,
    )
    config = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=1800.0,
        no_progress_quiet_seconds=240.0,
        no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        config,
        clock,
        process_monitor=monitor,
    )
    watchdog.record_invocation_start()
    return watchdog, clock


# A small local cap so the cache-eviction tests do not allocate the
# full production cap (128) of fakes on every poll. The eviction
# POLICY being tested is identical regardless of the cap value:
# LRU workers are evicted when the cap binds, and tombstoned
# workers are skipped on the next poll so stateful captures do
# not re-emit historical lines.
_TEST_CAP: int = 8


def test_subagent_capture_cache_is_hard_bounded_by_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-04 (iteration-4): the cache is a HARD FIFO bound.

    Drives the PUBLIC :meth:`IdleWatchdog.poll_subagent_output`
    with a monitor that returns cap+5 distinct workers on poll 1,
    then shrinks to just ``cap`` workers on poll 2. On poll 1 the
    cache MUST NOT grow past the cap even when every discovered
    worker is still live (the cap is not a soft bound on live
    workers). The 5 oldest workers MUST be evicted into the
    tombstone on poll 1 so the cache holds exactly ``cap`` entries.

    On poll 2 the 5 workers that disappeared from discovery are
    also released from the tombstone (they are no longer alive),
    leaving the cache at ``cap`` and the tombstone empty.
    """
    cap = _TEST_CAP

    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(cap + 5)}
    monitor = _FakeProcessMonitor(first_captures)
    watchdog, clock = _make_watchdog(monitor, cap, cap, monkeypatch)
    watchdog.poll_subagent_output(now=clock.monotonic())

    # Poll 1: the cap is HARD. All cap+5 workers are polled on this
    # tick (the cap is enforced at the END of the polling pass so
    # the public surface still reports every worker's lines), but
    # the cache ends at exactly ``cap`` entries and the 5 oldest
    # workers are moved to the tombstone.
    assert len(watchdog._subagent_output_captures) == cap, (
        f"hard cap MUST enforce cache at exactly cap={cap}, "
        f"got {len(watchdog._subagent_output_captures)}"
    )
    assert len(watchdog._evicted_worker_tombstones) == 5, (
        f"the 5 evicted workers MUST be tombstoned, "
        f"got {len(watchdog._evicted_worker_tombstones)}"
    )

    # Poll 2: the 5 oldest workers (w-0..w-4) disappear from
    # discovery. The cache MUST retain the surviving cap workers;
    # the tombstone MUST release the now-dead workers.
    surviving = {f"w-{i}": _StaticCaptureEmpty() for i in range(5, cap + 5)}
    monitor.replace_captures(surviving)
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0, (
        "tombstone MUST release entries for workers no longer in "
        "discovery (the eviction cooldown ended because the worker "
        "actually died)"
    )
    # The cap survivors MUST be retained in the cache.
    for index in range(5, cap + 5):
        assert f"w-{index}" in watchdog._subagent_output_captures


def test_subagent_capture_cache_does_not_evict_when_under_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inserts under the cap never evict anything."""
    cap = _TEST_CAP
    captures = {f"keep-{i}": _StaticCaptureEmpty() for i in range(cap // 2)}
    monitor = _FakeProcessMonitor(captures)

    watchdog, clock = _make_watchdog(monitor, cap, cap, monkeypatch)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog._subagent_output_captures) == cap // 2
    assert len(watchdog._evicted_worker_tombstones) == 0
    for index in range(cap // 2):
        assert f"keep-{index}" in watchdog._subagent_output_captures


def test_subagent_capture_cache_eviction_skips_existing_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated discover call for already-cached workers does NOT trigger eviction.

    Drives the PUBLIC entry point twice with the same worker
    set. The second call MUST NOT grow the cache (the existing
    workers are reused), and the cap MUST hold.
    """
    cap = _TEST_CAP

    primed = {f"primed-{i}": _StaticCaptureEmpty() for i in range(cap)}
    monitor = _FakeProcessMonitor(primed)
    watchdog, clock = _make_watchdog(monitor, cap, cap, monkeypatch)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0

    # Same workers, second tick: no new workers, no eviction.
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0


def test_subagent_capture_cache_polls_all_workers_then_enforces_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-04 (iteration-4): a poll with cap+5 workers reports cap+5 lines
    on the current tick and leaves the cache at exactly ``cap``.

    The cap is enforced at the END of the polling pass, so the
    public surface still reports EVERY worker's lines for the
    current tick (a high-fan-out tick is not a sampling cap).
    Only the next-tick cache state is bounded. ``_subagent_output_count``
    advances by exactly cap+5.
    """
    cap = _TEST_CAP

    captures = {f"w-{i}": _StaticCapture() for i in range(cap + 5)}
    monitor = _FakeProcessMonitor(captures)

    watchdog, clock = _make_watchdog(monitor, cap, cap, monkeypatch)
    fresh = watchdog.poll_subagent_output(now=clock.monotonic())

    # Every discovered worker's capture is read once (one line each).
    assert fresh == cap + 5
    # The watchdog records the total in the public counter.
    assert watchdog._subagent_output_count == cap + 5
    # The cache is HARD-bounded at ``cap`` after the polling pass.
    assert len(watchdog._subagent_output_captures) == cap, (
        f"hard cap MUST enforce cache at cap={cap}, "
        f"got {len(watchdog._subagent_output_captures)}"
    )
    # The 5 LRU workers were evicted into the tombstone.
    assert len(watchdog._evicted_worker_tombstones) == 5


class _StatefulCapture:
    """A SubagentOutputCapture that tracks its own read position.

    The production ``FileSubagentOutputCapture`` records a per-worker
    byte offset and only returns lines past that offset on the next
    poll. This fake mirrors that contract: first ``read_lines`` returns
    a fixed list of lines, subsequent calls return ``[]`` because the
    read position has already advanced past the content.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self._read_position = 0
        self.read_count = 0

    def read_lines(self, worker_id: str) -> list[str]:
        del worker_id
        self.read_count += 1
        if self._read_position >= len(self._lines):
            return []
        # Advance the read position past the lines we are returning
        # so the NEXT poll on the same capture returns no new lines
        # (mirroring the production file-position contract).
        slice_ = self._lines[self._read_position :]
        self._read_position = len(self._lines)
        return list(slice_)


def test_subagent_capture_tombstone_prevents_duplicate_output_after_eviction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-04 (iteration-4): the tombstone prevents duplicate output when
    LRU eviction + re-addition would otherwise re-read historical lines.

    Drives the PUBLIC entry point twice with cap+5 LIVE workers
    (all still alive on the second poll). The hard cap MUST evict
    5 workers on poll 1 and tombstone them so they cannot re-enter
    the cache on poll 2 (where their stateful read position has
    been lost). Poll 2 MUST report ZERO new lines because:
      * the cap survivors in cache are stateful (second read = 0)
      * the 5 tombstoned workers are skipped entirely
    Without the tombstone the 5 evicted workers would be re-added
    with fresh captures and re-read every historical line, exactly
    the duplicate-line bug the iteration-3 dead-worker eviction
    was trying to avoid.
    """
    cap = _TEST_CAP

    # Build cap+5 distinct workers, each returning N lines on the
    # FIRST read_lines call. With hard FIFO + tombstone, the 5 LRU
    # workers are evicted and tombstoned; the remaining cap workers
    # are polled and report their 3 lines each.
    line_count_per_worker = 3
    captures: dict[str, _StatefulCapture] = {}
    for i in range(cap + 5):
        lines = [f"line-{j}-worker-{i}" for j in range(line_count_per_worker)]
        captures[f"w-{i}"] = _StatefulCapture(lines)

    monitor = _FakeProcessMonitor(captures)
    watchdog, clock = _make_watchdog(monitor, cap, cap, monkeypatch)

    first_poll = watchdog.poll_subagent_output(now=clock.monotonic())
    # Poll 1 polls every discovered worker (cap enforcement happens
    # AFTER polling so every worker's lines are reported).
    expected_first = (cap + 5) * line_count_per_worker
    assert first_poll == expected_first, (
        f"first poll MUST report every line from every worker, got {first_poll} "
        f"vs expected {expected_first}"
    )
    # Cache ends at exactly ``cap`` (hard bound enforced).
    assert len(watchdog._subagent_output_captures) == cap
    # The 5 LRU workers are in the tombstone.
    assert len(watchdog._evicted_worker_tombstones) == 5

    # All 13 workers are still alive on poll 2.
    # A CORRECT implementation must skip the tombstoned workers
    # (so they do not re-emit historical lines) AND return 0 from
    # the cap survivors' stateful captures.
    clock.advance(0.01)
    second_poll = watchdog.poll_subagent_output(now=clock.monotonic())
    assert second_poll == 0, (
        f"second poll on the same live workers MUST report 0 new lines "
        f"(stateful capture read positions must be honored AND "
        f"tombstoned workers must be skipped); got {second_poll}"
    )
    # Cache is still at the cap.
    assert len(watchdog._subagent_output_captures) == cap
    # Tombstone is still populated because the evicted workers are
    # still alive (the tombstone is the cooldown that suppresses
    # their re-addition; it cycles out when the workers actually
    # exit OR when the tombstone cap binds and evicts LRU).
    assert len(watchdog._evicted_worker_tombstones) == 5


def test_subagent_capture_tombstone_cycles_out_when_worker_dies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a tombstoned worker actually exits, the tombstone releases its entry.

    The tombstone is the eviction cooldown that prevents re-addition
    of evicted workers (which would re-emit historical lines). Once
    a tombstoned worker actually disappears from
    ``discover_subagent_outputs``, the cooldown is no longer
    needed and the entry is released so the next time the same
    worker ID appears it can be re-added cleanly.
    """
    cap = _TEST_CAP

    # First poll: cap+5 workers. 5 are evicted and tombstoned.
    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(cap + 5)}
    monitor = _FakeProcessMonitor(first_captures)
    watchdog, clock = _make_watchdog(monitor, cap, cap, monkeypatch)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._evicted_worker_tombstones) == 5

    # Second poll: the 5 tombstoned workers are gone from
    # discovery. Their tombstone entries MUST be released.
    surviving = {f"w-{i}": _StaticCaptureEmpty() for i in range(5, cap + 5)}
    monitor.replace_captures(surviving)
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._evicted_worker_tombstones) == 0, (
        "tombstone MUST release entries when the worker actually "
        "exits (no longer in discovery)"
    )


def test_subagent_capture_tombstone_is_itself_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tombstone is bounded at ``_MAX_EVICTED_TOMBSTONES`` via FIFO eviction.

    A long-lived watchdog tick that keeps evicting LRU workers
    cannot grow the tombstone past its cap. FIFO eviction from
    the tombstone mirrors the cache eviction policy.
    """
    cap = _TEST_CAP
    tombstone_cap = 3  # Smaller than ``cap`` so the tombstone cap binds first.

    # Insert cap+5 workers; 5 are tombstoned. The tombstone cap
    # is 3, so the oldest 2 of the 5 evicted workers are dropped
    # from the tombstone at the end of the eviction step.
    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(cap + 5)}
    monitor = _FakeProcessMonitor(first_captures)
    watchdog, clock = _make_watchdog(monitor, cap, tombstone_cap, monkeypatch)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog._evicted_worker_tombstones) == tombstone_cap
    # The cap MUST still hold.
    assert len(watchdog._subagent_output_captures) == cap
    # The tombstone MUST hold the MOST RECENTLY evicted workers
    # (FIFO: the oldest tombstone entries are dropped first).
    # ``cap + 5 - cap = 5`` workers were evicted (the cache cap binds).
    # Tombstone cap is 3 so the 2 oldest evictions (w-0, w-1) are
    # dropped, leaving w-2, w-3, w-4 in the tombstone.
    num_evicted = (cap + 5) - cap
    tombstoned = list(watchdog._evicted_worker_tombstones.keys())
    assert tombstoned == [
        f"w-{i}" for i in range(num_evicted - tombstone_cap, num_evicted)
    ]
