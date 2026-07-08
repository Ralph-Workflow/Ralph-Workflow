"""Black-box tests for the HARD-FIFO-bounded subagent output capture cache.

wt-024 Step 6 (AC-04): ``_subagent_output_captures`` in
``IdleWatchdog`` is an ``OrderedDict`` capped at the production
``_MAX_SUBAGENT_OUTPUT_CAPTURES`` (defined in
``ralph.agents.idle_watchdog._activity_methods``). Inserting more
than the cap evicts the OLDEST-INSERTED entry so a single
high-fan-out watchdog tick that sees many distinct worker IDs
cannot grow the dict unboundedly within one invocation. The cap
uses pure FIFO eviction -- there is no LRU refresh on poll -- so
the bound holds across an entire invocation regardless of how
many times each worker is polled.

The cap is a HARD bound: when the cap binds, the oldest-inserted
worker is evicted regardless of whether it is still live or not.
To preserve the no-duplicate-output property of stateful
``SubagentOutputCapture`` implementations (the production
``FileSubagentOutputCapture`` tracks a per-worker byte offset
and would otherwise re-read historical lines if recreated from
offset 0 after eviction), evicted worker IDs are recorded in a
bounded ``_evicted_worker_tombstones`` map. Tombstoned workers
are skipped on the next poll so they cannot immediately re-enter
the cache and re-emit historical lines. The tombstone is itself
bounded using FIFO eviction.

The tests drive the production PUBLIC entry point
:meth:`IdleWatchdog.poll_subagent_output` with a fake
``ProcessMonitor`` whose ``discover_subagent_outputs`` returns a
deterministic mapping of fresh worker_ids to fresh
``SubagentOutputCapture`` instances on every call. No direct
``OrderedDict`` mutation, no direct ``_subagent_output_captures``
assignment, no real subprocess, no real file I/O, no
``time.sleep``. ``FakeClock`` drives the watchdog's clock
deterministically.

The cap is PRIVATE to ``_activity_methods`` and is not exposed on
the public ``IdleWatchdog`` constructor (policy file rules block
private ``ralph.agents.idle_watchdog._activity_methods`` imports
from tests). Tests therefore DERIVE the cap from observed
behavior at runtime via :func:`_probe_cache_cap` and
:func:`_probe_tombstone_cap`. Each probe polls with a worker
count that exceeds the largest plausible cap; the post-poll
collection size IS the cap. This pins the actual production cap
without hardcoding it in two places.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
)
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.process.monitor._subagent_output_capture import SubagentOutputCapture


# Worker count used to probe the cache and tombstone caps from
# observed behavior. The probe is generous (well above any
# plausible production cap) so the post-poll collection size IS
# the cap, regardless of whether the cap is 32, 128, 256, or any
# other value the production code chooses.
_PROBE_WORKER_COUNT: int = 4096


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
) -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog with the production cap.

    The ``IdleWatchdog`` public constructor exposes no cap override;
    the cap is a private module-level constant in
    ``_activity_methods``. Tests exercise the bound by generating
    enough distinct workers to overflow the production cap.
    """
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


def _probe_cache_cap() -> int:
    """Probe the production ``_MAX_SUBAGENT_OUTPUT_CAPTURES`` from observed behavior.

    The cap is a PRIVATE module-level constant in
    ``_activity_methods`` and cannot be imported directly from
    tests (policy file rules forbid private ralph imports). This
    helper drives a fresh watchdog with a worker count well above
    any plausible cap (``_PROBE_WORKER_COUNT`` = 4096) and
    returns the post-poll ``_subagent_output_captures`` size. The
    production code applies the HARD FIFO cap AT THE END of the
    polling pass, so the post-poll size IS the cap regardless of
    how many workers were polled. The probe is deterministic and
    safe to call from any test that needs the cap.
    """
    captures = {f"probe-{i}": _StaticCaptureEmpty() for i in range(_PROBE_WORKER_COUNT)}
    monitor = _FakeProcessMonitor(captures)
    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    return len(watchdog._subagent_output_captures)


def _probe_tombstone_cap() -> int:
    """Probe the production ``_MAX_EVICTED_TOMBSTONES`` from observed behavior.

    Drives a fresh watchdog with ``_PROBE_WORKER_COUNT`` distinct
    workers on the FIRST poll (so the cache evicts to its cap and
    every evicted worker is tombstoned). The post-poll
    ``_evicted_worker_tombstones`` size IS the tombstone cap --
    the production code bounds the tombstone at the END of the
    eviction pass via FIFO, so any tombstone entry beyond the cap
    is dropped before the poll returns.
    """
    captures = {f"probe-{i}": _StaticCaptureEmpty() for i in range(_PROBE_WORKER_COUNT)}
    monitor = _FakeProcessMonitor(captures)
    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    return len(watchdog._evicted_worker_tombstones)


# Worker counts that overflow the production caps by exactly five
# entries (cache cap binding) or by enough to overflow the
# tombstone cap (tombstone cap binding). Five is the smallest
# delta that keeps the test name readable; the eviction POLICY
# being tested is identical regardless of the cap overflow.
_OVERFLOW_DELTA: int = 5


def test_subagent_capture_cache_is_hard_bounded_by_cap() -> None:
    """AC-04 (iteration-4): the cache is a HARD FIFO bound.

    Drives the PUBLIC :meth:`IdleWatchdog.poll_subagent_output`
    with a monitor that returns cap+5 distinct workers on poll 1,
    then shrinks to just ``cap`` workers on poll 2. On poll 1 the
    cache MUST NOT grow past the cap even when every discovered
    worker is still live (the cap is not a soft bound on live
    workers). The 5 oldest-inserted workers MUST be evicted into
    the tombstone on poll 1 so the cache holds exactly ``cap``
    entries (pure FIFO; no LRU refresh on poll).

    On poll 2 the 5 workers that disappeared from discovery are
    also released from the tombstone (they are no longer alive),
    leaving the cache at ``cap`` and the tombstone empty.

    The cap is DERIVED from observed behavior via
    :func:`_probe_cache_cap` rather than hardcoded, so a future
    change to the production cap does not silently drift the
    test away from the actual bound.
    """
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitor(first_captures)
    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    # Poll 1: the cap is HARD. All cap+5 workers are polled on this
    # tick (the cap is enforced at the END of the polling pass so
    # the public surface still reports every worker's lines), but
    # the cache ends at exactly ``cap`` entries and the 5 oldest-
    # inserted workers are moved to the tombstone.
    assert len(watchdog._subagent_output_captures) == cap, (
        f"hard cap MUST enforce cache at exactly cap={cap}, "
        f"got {len(watchdog._subagent_output_captures)}"
    )
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA, (
        f"the {_OVERFLOW_DELTA} evicted workers MUST be tombstoned, "
        f"got {len(watchdog._evicted_worker_tombstones)}"
    )

    # Poll 2: the _OVERFLOW_DELTA oldest-inserted workers
    # (w-0..w-4) disappear from discovery. The cache MUST retain
    # the surviving cap workers; the tombstone MUST release the
    # now-dead workers.
    surviving = {f"w-{i}": _StaticCaptureEmpty() for i in range(_OVERFLOW_DELTA, first_count)}
    monitor.replace_captures(surviving)
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0, (
        "tombstone MUST release entries for workers no longer in "
        "discovery (the eviction cooldown ended because the worker "
        "actually died)"
    )
    # Every cap survivor MUST be retained in the cache. The
    # surviving set is ``range(_OVERFLOW_DELTA, first_count)``
    # (== ``range(_OVERFLOW_DELTA, cap + _OVERFLOW_DELTA)``), so
    # there are exactly ``cap`` survivors and we MUST assert on
    # every one of them (a partial iteration would silently miss
    # the boundary case where the last inserted survivor was
    # dropped despite still being alive).
    expected_survivors = {f"w-{i}" for i in range(_OVERFLOW_DELTA, first_count)}
    assert expected_survivors == set(watchdog._subagent_output_captures.keys()), (
        f"every cap survivor MUST be retained in the cache; "
        f"missing={expected_survivors - set(watchdog._subagent_output_captures.keys())}, "
        f"extra={set(watchdog._subagent_output_captures.keys()) - expected_survivors}"
    )


def test_subagent_capture_cache_does_not_evict_when_under_cap() -> None:
    """Inserts under the cap never evict anything."""
    cap = _probe_cache_cap()
    under_cap_count = cap // 2
    captures = {f"keep-{i}": _StaticCaptureEmpty() for i in range(under_cap_count)}
    monitor = _FakeProcessMonitor(captures)

    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog._subagent_output_captures) == under_cap_count
    assert len(watchdog._evicted_worker_tombstones) == 0
    for index in range(under_cap_count):
        assert f"keep-{index}" in watchdog._subagent_output_captures


def test_subagent_capture_cache_eviction_skips_existing_workers() -> None:
    """A repeated discover call for already-cached workers does NOT trigger eviction.

    Drives the PUBLIC entry point twice with the same worker
    set. The second call MUST NOT grow the cache (the existing
    workers are reused), and the cap MUST hold. Pure-FIFO means
    a repeated poll does NOT refresh the LRU position -- a worker
    that is polled repeatedly is NOT promoted to the most-recent;
    it stays in its original insertion position. This pins the
    FIFO contract so a future refactor cannot silently switch
    the cache to LRU semantics.
    """
    cap = _probe_cache_cap()

    primed = {f"primed-{i}": _StaticCaptureEmpty() for i in range(cap)}
    monitor = _FakeProcessMonitor(primed)
    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0

    # Same workers, second tick: no new workers, no eviction.
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0


def test_subagent_capture_cache_polls_all_workers_then_enforces_cap() -> None:
    """AC-04 (iteration-4): a poll with cap+5 workers reports cap+5 lines
    on the current tick and leaves the cache at exactly ``cap``.

    The cap is enforced at the END of the polling pass, so the
    public surface still reports EVERY worker's lines for the
    current tick (a high-fan-out tick is not a sampling cap).
    Only the next-tick cache state is bounded. ``_subagent_output_count``
    advances by exactly cap+5.
    """
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    captures = {f"w-{i}": _StaticCapture() for i in range(first_count)}
    monitor = _FakeProcessMonitor(captures)

    watchdog, clock = _make_watchdog(monitor)
    fresh = watchdog.poll_subagent_output(now=clock.monotonic())

    # Every discovered worker's capture is read once (one line each).
    assert fresh == first_count
    # The watchdog records the total in the public counter.
    assert watchdog._subagent_output_count == first_count
    # The cache is HARD-bounded at ``cap`` after the polling pass.
    assert len(watchdog._subagent_output_captures) == cap, (
        f"hard cap MUST enforce cache at cap={cap}, got {len(watchdog._subagent_output_captures)}"
    )
    # The _OVERFLOW_DELTA oldest-inserted workers were evicted into
    # the tombstone.
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA


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


def test_subagent_capture_tombstone_prevents_duplicate_output_after_eviction() -> None:
    """AC-04 (iteration-4): the tombstone prevents duplicate output when
    FIFO eviction + re-addition would otherwise re-read historical lines.

    Drives the PUBLIC entry point twice with cap+5 LIVE workers
    (all still alive on the second poll). The hard FIFO cap MUST
    evict _OVERFLOW_DELTA workers on poll 1 and tombstone them so
    they cannot re-enter the cache on poll 2 (where their stateful
    read position has been lost). Poll 2 MUST report ZERO new lines
    because:
      * the cap survivors in cache are stateful (second read = 0)
      * the _OVERFLOW_DELTA tombstoned workers are skipped entirely
    Without the tombstone the evicted workers would be re-added
    with fresh captures and re-read every historical line, exactly
    the duplicate-line bug the iteration-3 dead-worker eviction
    was trying to avoid.
    """
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    # Build cap+5 distinct workers, each returning N lines on the
    # FIRST read_lines call. With hard FIFO + tombstone, the
    # _OVERFLOW_DELTA oldest-inserted workers are evicted and
    # tombstoned; the remaining cap workers are polled and report
    # their 3 lines each.
    line_count_per_worker = 3
    captures: dict[str, _StatefulCapture] = {}
    for i in range(first_count):
        lines = [f"line-{j}-worker-{i}" for j in range(line_count_per_worker)]
        captures[f"w-{i}"] = _StatefulCapture(lines)

    monitor = _FakeProcessMonitor(captures)
    watchdog, clock = _make_watchdog(monitor)

    first_poll = watchdog.poll_subagent_output(now=clock.monotonic())
    # Poll 1 polls every discovered worker (cap enforcement happens
    # AFTER polling so every worker's lines are reported).
    expected_first = first_count * line_count_per_worker
    assert first_poll == expected_first, (
        f"first poll MUST report every line from every worker, got {first_poll} "
        f"vs expected {expected_first}"
    )
    # Cache ends at exactly ``cap`` (hard bound enforced).
    assert len(watchdog._subagent_output_captures) == cap
    # The _OVERFLOW_DELTA oldest-inserted workers are in the tombstone.
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA

    # All first_count workers are still alive on poll 2.
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
    # exit OR when the tombstone cap binds and evicts the oldest-
    # inserted).
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA


def test_subagent_capture_tombstone_cycles_out_when_worker_dies() -> None:
    """When a tombstoned worker actually exits, the tombstone releases its entry.

    The tombstone is the eviction cooldown that prevents re-addition
    of evicted workers (which would re-emit historical lines). Once
    a tombstoned worker actually disappears from
    ``discover_subagent_outputs``, the cooldown is no longer
    needed and the entry is released so the next time the same
    worker ID appears it can be re-added cleanly.
    """
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    # First poll: cap+5 workers. _OVERFLOW_DELTA are evicted and
    # tombstoned.
    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitor(first_captures)
    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA

    # Second poll: the _OVERFLOW_DELTA tombstoned workers are gone
    # from discovery. Their tombstone entries MUST be released.
    surviving = {f"w-{i}": _StaticCaptureEmpty() for i in range(_OVERFLOW_DELTA, first_count)}
    monitor.replace_captures(surviving)
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._evicted_worker_tombstones) == 0, (
        "tombstone MUST release entries when the worker actually exits (no longer in discovery)"
    )


def test_subagent_capture_tombstone_is_itself_bounded() -> None:
    """The tombstone is bounded at the production cap via FIFO eviction.

    A long-lived watchdog tick that keeps evicting FIFO workers
    cannot grow the tombstone past its cap. FIFO eviction from
    the tombstone mirrors the cache eviction policy: the oldest-
    inserted entry is dropped first so the most-recently-evicted
    workers retain their cooldown priority.

    This test overflows BOTH the cache cap and the tombstone cap
    in a single poll so the tombstone cap binding is exercised.
    The worker count is ``cap + cap + 1`` so after cache eviction
    ``cap + 1`` workers are in the tombstone and the tombstone cap
    binds, dropping the oldest-inserted entry.
    """
    cap = _probe_cache_cap()
    tombstone_cap = _probe_tombstone_cap()
    # cap + cap + 1 workers: cache evicts to cap, leaving cap+1
    # evicted entries; tombstone evicts to cap, leaving the
    # newest cap entries (w-1..w-cap).
    first_count = cap + cap + 1

    # Insert cap+cap+1 workers; cap+1 are tombstoned (cache evicts
    # to cap). The tombstone cap binds at ``cap``, so the oldest
    # 1 of the cap+1 evicted workers is dropped from the
    # tombstone at the end of the eviction step.
    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitor(first_captures)
    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog._evicted_worker_tombstones) == tombstone_cap
    # The cap MUST still hold.
    assert len(watchdog._subagent_output_captures) == cap
    # The tombstone MUST hold the MOST RECENTLY evicted workers
    # (FIFO: the oldest-inserted tombstone entries are dropped
    # first). ``first_count - cap = cap + 1`` workers were evicted
    # from the cache. The tombstone cap (``cap``) drops the oldest
    # 1 of those entries (w-0), leaving w-1..w-cap in the
    # tombstone.
    num_evicted = first_count - cap
    tombstoned = list(watchdog._evicted_worker_tombstones.keys())
    assert tombstoned == [f"w-{i}" for i in range(num_evicted - tombstone_cap, num_evicted)]
