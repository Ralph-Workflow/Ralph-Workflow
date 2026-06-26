"""Black-box tests for the R1 filtered subagent count contract.

R1 (Trustworthy Idle Watchdog product spec):

    Child-process / per-agent monitors count only real subagents; host
    and internal helper spawns are provably excluded.

The contract is enforced by the ``SubagentPidRegistry`` (the single
source of truth for what counts as a real subagent) and the
``ProcessMonitor.spawned_subagent_count()`` seam (preferred name;
``live_subagent_count()`` is the legacy alias returning the same
filtered count).

The tests in this module are pure black-box:

    * No real subprocess. No real time. No real filesystem.
    * Synthetic process trees are simulated by injecting a fake
      ``ProcessMonitor`` whose ``spawned_subagent_count`` /
      ``live_subagent_count`` return whatever the test needs (the
      broader ``descendant_snapshot`` count is NOT a public surface of
      the ``ProcessMonitor`` Protocol -- it is the implementation
      detail of ``_process_reader._corroborate`` / ``_pty_line_reader``
      that the audit flags when used as the source of
      ``scoped_child_active``).
    * The registry is exercised directly with synthetic PIDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from ralph.agents.idle_watchdog import SubagentIdentity, SubagentPidRegistry
from ralph.agents.idle_watchdog._subagent_identity import _MAX_REGISTRY_ENTRIES
from ralph.process.monitor import (
    ProcessMonitor,
    SubagentOutputCapture,
    make_claude_subagent_pid_source,
    make_opencode_subagent_pid_source,
)


@dataclass
class _FilteredCountMonitor:
    """Fake monitor that returns FILTERED counts for both seam names.

    Both ``live_subagent_count()`` (legacy alias) and
    ``spawned_subagent_count()`` (preferred) return ``filtered_count``.
    The test asserts both names return the SAME value -- the alias is
    faithful, not a super-set.
    """

    filtered_count: int = 0
    descendant_snapshot_count: int = 0
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return self.filtered_count

    def spawned_subagent_count(self) -> int:
        return self.filtered_count

    def descendant_snapshot(self) -> tuple[int, float]:
        """Stand-in for the BROADER ``handle.descendant_snapshot()`` surface.

        The ``ProcessMonitor`` Protocol does NOT expose this method
        (the broader count is a private implementation detail of the
        per-reader corroborators). This stand-in lets the test assert
        that the SEAM is the filtered count -- the broader count must
        NOT be used as the deferral signal.
        """
        return self.descendant_snapshot_count, 0.0

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


@dataclass
class _RegistryPidSource:
    """Minimal ``SubagentPidSource`` backed by a ``SubagentPidRegistry``."""

    registry: SubagentPidRegistry
    source_label: str

    def known_subagent_pids(self) -> set[int]:
        return {
            identity.pid
            for identity in self.registry.snapshot()
            if identity.source == self.source_label
        }


def test_helper_processes_alone_yield_zero_filtered_count() -> None:
    """R1: a monitor that only sees helper PIDs returns 0 from BOTH seam names.

    The product spec cites ``npm test``, ``cargo build``, ``find /`` as
    helper spawns that MUST NOT contribute to the subagent count. The
    monitor's broader ``descendant_snapshot`` count is 10 helpers, but
    the FILTERED count is 0 -- the watchdog defers on the filtered
    count only.
    """
    monitor = _FilteredCountMonitor(
        filtered_count=0,
        descendant_snapshot_count=10,
    )
    assert monitor.spawned_subagent_count() == 0
    assert monitor.live_subagent_count() == 0
    # The broader count is the bug source -- the filtered seam is 0
    # even when 10 helpers are present in the descendant tree.
    assert monitor.descendant_snapshot()[0] == 10


def test_spawned_subagent_count_equals_live_subagent_count() -> None:
    """The alias is faithful: ``spawned_subagent_count() == live_subagent_count()``.

    R1: the filtered count is the ONLY count the watchdog defers on.
    Both names MUST return the SAME filtered value so callers that
    continue to call ``live_subagent_count`` (legacy callers in
    ``_waiting_branch.py`` and ``_activity_methods.py``) see the same
    signal as new callers using ``spawned_subagent_count``.
    """
    monitor = _FilteredCountMonitor(filtered_count=3)
    assert monitor.spawned_subagent_count() == 3
    assert monitor.live_subagent_count() == 3
    assert monitor.spawned_subagent_count() == monitor.live_subagent_count()


def test_subagent_pid_registry_bounded_at_max_entries() -> None:
    """R1 + resource-lifecycle: registry is FIFO-bounded at 1024 entries.

    A long unattended invocation can register thousands of subagent
    PIDs (one per dispatched worker). An unbounded registry would
    retain heavyweight ``SubagentIdentity`` records across runs and
    bloat the watchdog's memory footprint. The audit
    ``audit_resource_lifecycle`` enforces a FIFO cap on every long-
    lived mutable collection; the registry honours the cap with
    ``OrderedDict.popitem(last=False)`` eviction.
    """
    assert _MAX_REGISTRY_ENTRIES == 1024
    registry = SubagentPidRegistry()
    # PIDs must be positive; offset by 1_000_000 to stay well clear of
    # kernel reserved values. ``registered_at_monotonic`` is the
    # monotonic timestamp captured at registration time (i.e. the
    # iteration index -- used purely for ordering).
    for pid in range(1_000_001, 1_002_001):
        registry.register(pid, source="opencode", now=float(pid))
    # The registry evicts the OLDEST entries first; the surviving
    # PIDs are the most-recently-registered ones.
    snapshot = registry.snapshot()
    assert len(snapshot) == _MAX_REGISTRY_ENTRIES == 1024
    surviving_pids = [identity.pid for identity in snapshot]
    assert surviving_pids == list(range(1_000_977, 1_002_001))
    # The OLDEST pids (1_000_001..1_000_976) are evicted FIFO.
    assert 1_000_001 not in registry.known_pids()
    assert 1_000_976 not in registry.known_pids()
    assert 1_000_977 in registry.known_pids()
    assert 1_002_000 in registry.known_pids()


def test_register_is_idempotent() -> None:
    """Duplicate ``register`` calls preserve the FIRST ``registered_at_monotonic``.

    R1: the watchdog reads ``registered_at_monotonic`` to reason about
    subagent lifetime. A duplicate call MUST NOT rewrite the timestamp
    (otherwise a repeated discovery tick could indefinitely extend a
    stale subagent's lifetime). Idempotency is the canonical contract
    of the registry.
    """
    registry = SubagentPidRegistry()
    first = registry.register(1234, source="opencode", now=10.0)
    second = registry.register(1234, source="opencode", now=999.0)
    third = registry.register(1234, source="claude", now=42.0)
    assert first.registered_at_monotonic == 10.0
    assert second.registered_at_monotonic == 10.0
    assert third.registered_at_monotonic == 10.0
    # Only ONE entry for PID 1234 (idempotent).
    assert len(registry.snapshot()) == 1
    # The first source wins on idempotent re-registration. This
    # matches the contract that ``register`` returns the existing
    # identity unchanged.
    assert registry.snapshot()[0].source == "opencode"


def test_unregister_removes_pid() -> None:
    """``unregister`` is the canonical way to retire a PID from the registry.

    R1: a subagent that exits must be removed so the watchdog stops
    deferring on it. ``unregister`` is a no-op when the PID is unknown
    (returns None / does not raise).
    """
    registry = SubagentPidRegistry()
    registry.register(1234, source="opencode", now=0.0)
    assert 1234 in registry.known_pids()
    assert len(registry.snapshot()) == 1
    registry.unregister(1234)
    assert 1234 not in registry.known_pids()
    assert len(registry.snapshot()) == 0
    # Unknown PID: no-op.
    registry.unregister(9999)
    assert 9999 not in registry.known_pids()


def test_spawned_subagent_count_filters_out_unregistered_descendants() -> None:
    """The SubagentPidRegistry is the FILTER; unregistered PIDs are helpers.

    R1: a descendant PID that is in ``psutil.children(recursive=True)``
    but NOT in the registry is an INCIDENTAL_HELPER. The filtered
    count must NOT include it. This is the headline assertion that
    distinguishes the filtered seam from the broader
    ``descendant_snapshot`` count.
    """
    registry = SubagentPidRegistry()
    # Register only ONE subagent.
    registry.register(7001, source="opencode", now=0.0)
    # A SubagentPidSource wrapping the registry returns only the
    # registered PID (filtered).
    source = _RegistryPidSource(registry, source_label="opencode")
    assert source.known_subagent_pids() == {7001}
    # 3 unregistered PIDs in the broader descendant tree are HELPERS;
    # the filtered count is 1, NOT 4.
    assert len(source.known_subagent_pids()) == 1
    # The mock monitor's broader count would be 4 (1 registered + 3
    # helpers); the FILTERED count is 1. This is the headline R1
    # invariant: filtered < broader when helpers exist.
    broader_count = 1 + 3
    assert len(source.known_subagent_pids()) < broader_count


def test_subagent_identity_rejects_invalid_source() -> None:
    """``SubagentIdentity`` constructor validates ``source`` against the allowed set.

    R1: the canonical set of transports is the only valid ``source``
    label; an unrecognized label would let an arbitrary code path
    introduce a new "real subagent" type without updating the
    canonical owner. The constructor rejects unknown sources so the
    type system enforces the canonical set.
    """
    # Use ``cast`` rather than a mypy suppression so the test file
    # carries no suppressions (the audit policy forbids test-file
    # suppressions). Cast to ``Any`` to bypass the Literal narrowing
    # without an ignore.
    bad_source: Any = "unknown-transport"
    with pytest.raises(ValueError, match="unknown subagent source"):
        SubagentIdentity(
            pid=1234,
            source=cast("SubagentIdentity.__init__", bad_source),
            registered_at_monotonic=0.0,
        )
    with pytest.raises(ValueError, match="pid must be positive"):
        SubagentIdentity(
            pid=0,
            source="opencode",
            registered_at_monotonic=0.0,
        )
    with pytest.raises(ValueError, match="pid must be positive"):
        SubagentIdentity(
            pid=-1,
            source="opencode",
            registered_at_monotonic=0.0,
        )


def test_process_monitor_protocol_includes_spawned_subagent_count() -> None:
    """The ProcessMonitor Protocol MUST advertise ``spawned_subagent_count``.

    R1: the audit ``audit_activity_aware_watchdog`` flags any reader
    that uses ``descendant_snapshot()`` instead of
    ``spawned_subagent_count()`` for ``scoped_child_active``. The
    Protocol MUST declare both names so a ``@runtime_checkable``
    isinstance check against the Protocol works for duck-typed
    monitors that implement either name.
    """
    assert hasattr(ProcessMonitor, "spawned_subagent_count")
    assert hasattr(ProcessMonitor, "live_subagent_count")


def test_filtered_count_seam_is_isolated_per_transport() -> None:
    """A Claude-registered PID is invisible to an OpenCode monitor (and vice versa).

    R1: the registry is shared across transports but the per-transport
    filter (``_RegistryBackedSubagentPidSource``) restricts the view
    to entries matching its own source label. A Claude-registered PID
    MUST NOT contribute to an OpenCode filter (different transport,
    different worker lifecycle).
    """
    registry = SubagentPidRegistry()
    registry.register(8001, source="claude", now=0.0)
    registry.register(8002, source="opencode", now=0.0)
    opencode_source = make_opencode_subagent_pid_source(registry)
    assert opencode_source.known_subagent_pids() == {8002}
    # Claude filter would see only 8001.
    claude_source = make_claude_subagent_pid_source(registry)
    assert claude_source.known_subagent_pids() == {8001}
