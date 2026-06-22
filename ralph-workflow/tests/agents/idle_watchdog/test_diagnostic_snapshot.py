"""Pin: IdleWatchdog.diagnostic_snapshot(now) public surface.

The watchdog kill -> recovery path needs the full watchdog state at
the moment of the fire for post-mortem analysis. The
``IdleWatchdogKilledError.evidence_summary`` carries the per-channel
evidence but not the broader watchdog state (last fire reason, last
deferred kind, cumulative waiting, monitor live count, subagent
progress description, etc.).

The fix: a public ``IdleWatchdog.diagnostic_snapshot(now)`` method
that returns a JSON-serializable dict of the watchdog's full state.
The post-mortem ``merged_diag`` in ``_process_reader._check_fire``
calls this method and merges the snapshot under the
``watchdog_snapshot`` key so the post-mortem log carries the full
state at the moment of the fire.

This test exercises every key in the snapshot dict and asserts the
values match the watchdog state after a known sequence of events.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
)
from ralph.agents.timeout_clock import FakeClock


@dataclass
class _FakeProcessMonitor:
    """Fake process monitor with a configurable live-subagent count."""

    count: int = 0

    def live_subagent_count(self) -> int:
        return self.count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


def _make_watchdog(
    *, monitor_count: int = 0
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=_FakeProcessMonitor(count=monitor_count),
        ),
        clock,
    )


def test_diagnostic_snapshot_has_all_required_keys() -> None:
    """The snapshot dict MUST contain every key documented in the
    method's docstring.
    """
    watchdog, _clock = _make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    required_keys = {
        "last_fire_reason",
        "last_deferred_kind",
        "last_alive_by",
        "idle_elapsed_seconds",
        "invocation_elapsed_seconds",
        "cumulative_waiting_on_child_seconds",
        "last_subagent_progress_description",
        "live_subagent_count",
        "subagent_progress_count",
        "subagent_output_count",
        "mcp_tool_call_count",
        "workspace_event_count",
        "evidence_summary",
        "resumable_session_id",
    }
    assert set(snapshot.keys()) >= required_keys, (
        f"snapshot keys missing: required - snapshot = {required_keys - set(snapshot.keys())}"
    )


def test_diagnostic_snapshot_is_json_serializable() -> None:
    """The snapshot dict MUST be JSON-serializable so it can be
    embedded in the merged_diag payload without further conversion.
    """
    watchdog, _clock = _make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    # Must not raise (json.dumps requires primitive types).
    encoded = json.dumps(snapshot)
    assert isinstance(encoded, str)
    # Round-trip the JSON to confirm the shape is preserved.
    decoded = json.loads(encoded)
    assert decoded["last_fire_reason"] is None
    assert decoded["last_deferred_kind"] is None


def test_diagnostic_snapshot_reflects_record_subagent_work() -> None:
    """After ``record_subagent_work`` the snapshot MUST carry the
    description AND increment the subagent_progress_count.
    """
    watchdog, clock = _make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    watchdog.record_subagent_work(description="reading source.py")
    snapshot = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot["last_subagent_progress_description"] == "reading source.py", (
        f"snapshot.last_subagent_progress_description MUST be"
        f" 'reading source.py'; got {snapshot['last_subagent_progress_description']!r}"
    )
    assert snapshot["subagent_progress_count"] == 1, (
        f"snapshot.subagent_progress_count MUST be 1; got"
        f" {snapshot['subagent_progress_count']}"
    )
    # idle_elapsed_seconds == 5.0
    assert snapshot["idle_elapsed_seconds"] == 5.0, (
        f"snapshot.idle_elapsed_seconds MUST be 5.0; got"
        f" {snapshot['idle_elapsed_seconds']}"
    )


def test_diagnostic_snapshot_live_subagent_count() -> None:
    """When a process monitor is injected with ``live_subagent_count=N``
    the snapshot MUST report ``live_subagent_count=N``.
    """
    watchdog, _clock = _make_watchdog(monitor_count=3)
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    assert snapshot["live_subagent_count"] == 3, (
        f"snapshot.live_subagent_count MUST be 3; got {snapshot['live_subagent_count']}"
    )


def test_diagnostic_snapshot_evidence_summary_has_channels() -> None:
    """The ``evidence_summary`` list MUST contain one entry per
    ``ChannelName`` (stdout, mcp_tool, subagent_output,
    subagent_liveness, workspace).
    """
    watchdog, _clock = _make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    summary = snapshot["evidence_summary"]
    assert isinstance(summary, list), (
        f"snapshot.evidence_summary MUST be a list; got {type(summary)}"
    )
    # 5 channels: stdout, mcp_tool, subagent_output, subagent_liveness, workspace.
    assert len(summary) == 5, (
        f"snapshot.evidence_summary MUST have 5 entries; got {len(summary)}"
    )


def test_diagnostic_snapshot_after_record_invocation_start_resets() -> None:
    """``record_invocation_start`` MUST clear the
    ``last_subagent_progress_description`` so the snapshot reflects the
    fresh invocation. (Per-channel counters are session-scoped and
    survive across invocations by design.)
    """
    watchdog, clock = _make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    watchdog.record_subagent_work(description="reading source.py")
    # Capture a snapshot showing the populated state.
    populated = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert populated["last_subagent_progress_description"] == "reading source.py"
    assert populated["subagent_progress_count"] == 1
    # Reset and capture again.
    watchdog.record_invocation_start()
    clock.advance(2.0)
    reset = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert reset["last_subagent_progress_description"] is None, (
        f"record_invocation_start MUST reset"
        f" last_subagent_progress_description; got"
        f" {reset['last_subagent_progress_description']!r}"
    )


def test_diagnostic_snapshot_is_pure_read_no_side_effects() -> None:
    """Calling ``diagnostic_snapshot`` MUST NOT mutate watchdog state.
    Two consecutive calls at the same clock value MUST return equal
    snapshots.
    """
    watchdog, clock = _make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    snapshot_a = watchdog.diagnostic_snapshot(now=clock.monotonic())
    snapshot_b = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot_a == snapshot_b, (
        f"diagnostic_snapshot MUST be a pure read; got {snapshot_a} vs {snapshot_b}"
    )


def test_diagnostic_snapshot_is_method_not_coroutine() -> None:
    """``diagnostic_snapshot`` MUST be a synchronous method, not a
    coroutine, so the watchdog-kill path can call it synchronously
    without awaiting.
    """
    watchdog, _clock = _make_watchdog()
    assert not inspect.iscoroutinefunction(watchdog.diagnostic_snapshot), (
        "diagnostic_snapshot MUST be a synchronous method"
    )


def test_diagnostic_snapshot_uses_injected_now_argument() -> None:
    """When ``now`` is passed explicitly the snapshot MUST use that
    timestamp so tests can drive FakeClock deterministically.
    """
    watchdog, clock = _make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    snapshot = watchdog.diagnostic_snapshot(now=42.5)
    assert snapshot["idle_elapsed_seconds"] == 42.5, (
        f"snapshot.idle_elapsed_seconds MUST use injected now; got"
        f" {snapshot['idle_elapsed_seconds']}"
    )


def test_diagnostic_snapshot_records_fire_reason() -> None:
    """After a fire the snapshot MUST carry the canonical
    ``WatchdogFireReason.value`` string so post-mortem logs
    can show the reason without coupling to private watchdog
    internals.

    Black-box: drive the watchdog through ``evaluate()`` with a
    short no_output_at_start threshold so the no-output fire path
    sets ``last_fire_reason`` naturally. ``diagnostic_snapshot``
    is then read via its public API.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=10.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_FakeProcessMonitor())
    watchdog.record_invocation_start()
    # Advance past the no_output_at_start threshold; no recorded
    # activity; ACTIVE classify_quiet returns the verdict path
    # straight to NO_OUTPUT_AT_START.
    clock.advance(11.0)
    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict.name == "FIRE", (
        f"watchdog.evaluate MUST fire NO_OUTPUT_AT_START after the"
        f" threshold with no activity; got verdict={verdict}"
    )
    snapshot = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot["last_fire_reason"] == "no_output_at_start", (
        f"snapshot.last_fire_reason MUST be 'no_output_at_start'; got"
        f" {snapshot['last_fire_reason']!r}"
    )
