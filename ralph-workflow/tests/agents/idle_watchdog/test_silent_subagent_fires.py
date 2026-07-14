"""Liveness regression: a silent subagent MUST NOT defer the watchdog forever.

The SILENT_SUBAGENT branch of ``classify_stuck`` matches a STRICT SUBSET of
the STUCK conditions: a subagent channel has evidence (``counter >= 1``), its
most recent signal is older than ``silent_subagent_seconds``, there is NO
live-child signal (``alive_by is None``), no fresh first-party evidence, and
``classify_quiet`` is ACTIVE. That is the definition of a dead agent.

The branch is checked BEFORE the STUCK fall-through, so it SHADOWS it. When
the gate treated SILENT_SUBAGENT as a non-FIRE label, the result was a
liveness inversion:

    60s of silence   -> STUCK           -> FIRE
    181s of silence  -> SILENT_SUBAGENT -> deferred
    24h of silence   -> SILENT_SUBAGENT -> deferred (forever)

Crossing the silence threshold moved a run from killable to permanently
immune, because the gate's only bypass (``SESSION_CEILING_EXCEEDED``) is
driven by ``max_session_seconds``, which defaults to None. A wedged run
emitted one throttled DEBUG line every 30s and never recovered.

Deferring is safe ONLY when there is reason to believe a child is alive.
``_silent_subagent_path`` requires ``alive_by is None`` -- i.e. the
corroborator sees NO live child. If a child were alive, the corroborator sets
``alive_by`` and the higher-priority LOADING branch wins first. So the gate
MUST fire on SILENT_SUBAGENT; the kind remains a post-mortem LABEL, not a
veto.

These tests pin the liveness invariant: **no classifier kind may defer a fire
unboundedly.**
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
from ralph.agents.timeout_clock import FakeClock


@dataclass
class _NoProcessMonitor:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


def _make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        silent_subagent_seconds=180.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())
    return watchdog, clock


def _wedge(watchdog: IdleWatchdog, clock: FakeClock, silent_for: float) -> float:
    """Dispatch a subagent, let it speak once, then go silent for ``silent_for``.

    Reproduces the production wedge: the subagent channel carries stale
    historical evidence and no live-child signal.
    """
    watchdog.record_invocation_start()
    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(silent_for)
    return clock.monotonic()


def test_gate_fires_on_silent_subagent() -> None:
    """The gate MUST fire when a subagent went silent with no live child.

    Deferring here is the liveness inversion: the classifier has POSITIVELY
    identified a dead agent, and that identification must not become the
    reason it is spared.
    """
    watchdog, clock = _make_watchdog()
    now = _wedge(watchdog, clock, silent_for=181.0)

    # Precondition: the classifier really does name this SILENT_SUBAGENT.
    assert watchdog._classify_stuck_now(now=now, idle_elapsed=181.0) == (
        StuckKind.SILENT_SUBAGENT
    )

    gate_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=181.0,
    )
    assert gate_verdict == WatchdogVerdict.FIRE, (
        "Gate MUST FIRE on SILENT_SUBAGENT: the branch requires alive_by is None,"
        f" so there is no live child to protect. Got {gate_verdict}."
    )


def test_silent_subagent_fire_is_not_recorded_as_a_deferral() -> None:
    """A fired SILENT_SUBAGENT is NOT a deferral: ``last_deferred_kind`` stays None.

    The kind survives as a post-mortem label via the log line and the real
    ``last_fire_reason`` the caller stamps; it must not masquerade as a
    deferral in the diagnostic surface.
    """
    watchdog, clock = _make_watchdog()
    now = _wedge(watchdog, clock, silent_for=181.0)

    watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE, now=now, idle_elapsed=181.0
    )
    assert watchdog.last_deferred_kind is None, (
        "A FIRE is not a deferral; last_deferred_kind must not be stamped."
        f" Got {watchdog.last_deferred_kind}."
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


def test_silence_never_becomes_permanent_immunity() -> None:
    """Liveness invariant: the deferral MUST NOT be unbounded.

    Sweeps the silence duration across the threshold. Before the fix this
    inverted: 60s fired, everything past 180s deferred forever. A watchdog
    that kills a one-minute stall but protects a 24-hour corpse is worse than
    no watchdog.
    """
    for silent_for in (61.0, 181.0, 600.0, 3600.0, 86_400.0):
        watchdog, clock = _make_watchdog()
        now = _wedge(watchdog, clock, silent_for=silent_for)

        gate_verdict = watchdog._gate_fire(
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            now=now,
            idle_elapsed=silent_for,
        )
        assert gate_verdict == WatchdogVerdict.FIRE, (
            f"Silence of {silent_for}s must FIRE, not defer. A longer stall can"
            " never be MORE deserving of protection than a shorter one."
        )
