"""Runtime-facing integration tests for the SILENT_SUBAGENT diagnostic.

The companion tests in ``tests/agents/idle_watchdog/test_stuck_classifier.py``
cover the pure ``classify_stuck`` function (the SILENT_SUBAGENT branch
in isolation).  These tests pin the RUNTIME seam: the watchdog's
``_classify_stuck_now`` MUST pass ``silent_subagent_seconds`` into the
classifier AND the gate MUST surface ``SILENT_SUBAGENT`` distinctly via
the ``last_deferred_kind`` property rather than collapsing every
deferral into ``DEFERRED_BY_STUCK_CLASSIFIER``.

The analysis-feedback contract (AC-05 + how_to_fix item #2):

* ``IdleWatchdog._classify_stuck_now`` MUST pass
  ``silent_subagent_seconds=self._config.silent_subagent_seconds`` into
  ``classify_stuck`` (production runtime path).
* ``classify_stuck`` still LABELS the stall ``StuckKind.SILENT_SUBAGENT``
  (the diagnostic survives).
* The gate FIRES on that label -- it is not a veto. The branch requires
  ``alive_by is None``, so no live child exists to protect; deferring it
  wedged runs forever. See ``test_silent_subagent_fires.py`` for the
  liveness invariant and ``_gate.py`` for the rationale.

Without this layer the SILENT_SUBAGENT diagnostic is invisible to
operators even when the underlying classifier branch fires.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
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


def _make_watchdog(
    *,
    silent_subagent_seconds: float | None = 180.0,
    activity_evidence_ttl_seconds: float | None = 30.0,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=activity_evidence_ttl_seconds,
        silent_subagent_seconds=silent_subagent_seconds,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())
    return watchdog, clock


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def test_silent_subagent_seconds_is_threaded_into_runtime_classifier() -> None:
    """``IdleWatchdog._classify_stuck_now`` MUST pass
    ``silent_subagent_seconds`` into the runtime classifier.

    Drives the production seam so a future refactor that drops the
    parameter from the call site surfaces immediately.  We assert
    via ``_classify_stuck_now`` return value rather than via the
    evaluate path because the gate's STUCK vs SILENT_SUBAGENT
    distinction is internal to ``classify_stuck``.
    """
    watchdog, clock = _make_watchdog(silent_subagent_seconds=180.0)
    watchdog.record_invocation_start()

    # Record a subagent_progress observation at 30s.
    clock.advance(30.0)
    watchdog.record_subagent_work(description="tool_use:Bash")

    # Advance past the silent_subagent_seconds window (180s).
    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    kind = watchdog._classify_stuck_now(now=now, idle_elapsed=181.0)
    assert kind == StuckKind.SILENT_SUBAGENT, (
        f"Expected SILENT_SUBAGENT when subagent_progress is stale and"
        f" classify_quiet is ACTIVE; got {kind}"
    )


def test_silent_subagent_disabled_when_silent_subagent_seconds_is_none() -> None:
    """When ``silent_subagent_seconds=None``, the runtime classifier
    MUST NOT return SILENT_SUBAGENT (the diagnostic is opt-in).

    Drives the production seam so the runtime threading of
    ``silent_subagent_seconds=None`` is verified: the classifier
    falls through to STUCK rather than SILENT_SUBAGENT.
    """
    watchdog, clock = _make_watchdog(silent_subagent_seconds=None)
    watchdog.record_invocation_start()

    clock.advance(30.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(1000.0)
    now = clock.monotonic()

    kind = watchdog._classify_stuck_now(now=now, idle_elapsed=1000.0)
    assert kind != StuckKind.SILENT_SUBAGENT, (
        f"Expected the SILENT_SUBAGENT diagnostic to be DISABLED when"
        f" silent_subagent_seconds is None; got {kind}"
    )


def test_gate_fires_on_silent_subagent_and_does_not_record_a_deferral() -> None:
    """The gate MUST FIRE when the classifier returns SILENT_SUBAGENT.

    Drives the production ``_gate_fire`` path. The classifier still LABELS
    the stall SILENT_SUBAGENT, but the label is not a veto: the branch
    requires ``alive_by is None`` (no live child), so there is nothing to
    protect. Deferring here shadowed the STUCK branch and wedged the run
    forever -- see ``test_silent_subagent_fires.py``.
    """
    watchdog, clock = _make_watchdog(
        silent_subagent_seconds=180.0,
        activity_evidence_ttl_seconds=30.0,
    )
    watchdog.record_invocation_start()

    # Record subagent progress at 30s (within no_output_at_start window).
    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")

    # Advance past the silent-subagent threshold (180s) AND the activity
    # evidence TTL (30s) so the channel is stale and the gate sees
    # SILENT_SUBAGENT rather than THINKING/LOADING.
    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    gate_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=clock.monotonic(),
    )
    assert gate_verdict == WatchdogVerdict.FIRE, (
        f"Gate MUST FIRE on SILENT_SUBAGENT (no live child); got {gate_verdict}"
    )
    assert watchdog.last_deferred_kind is None, (
        f"A FIRE is not a deferral; got last_deferred_kind={watchdog.last_deferred_kind}"
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


def test_last_deferred_kind_is_none_when_no_fire_deferred() -> None:
    """``last_deferred_kind`` MUST be ``None`` until the first deferral.

    The diagnostic is only meaningful AFTER a deferral; pre-deferral
    the field is ``None``.  Drives the production
    ``record_invocation_start`` reset path.
    """
    watchdog, _clock = _make_watchdog(silent_subagent_seconds=180.0)
    assert watchdog.last_deferred_kind is None
    watchdog.record_invocation_start()
    assert watchdog.last_deferred_kind is None


def test_last_deferred_kind_resets_on_invocation_start() -> None:
    """``last_deferred_kind`` MUST reset on a new invocation so a
    prior deferred diagnostic does not leak into a fresh run.
    """
    watchdog, clock = _make_watchdog(silent_subagent_seconds=180.0)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    # Force one deferral. SILENT_SUBAGENT now FIRES, so drive a kind that
    # still defers: is_waiting_state=True -> DUPLICATE_KILL (branch 1).
    watchdog.set_is_waiting_state(True)
    watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=clock.monotonic(),
    )
    assert watchdog.last_deferred_kind == StuckKind.DUPLICATE_KILL

    # A new invocation MUST reset the deferred kind so prior
    # deferrals don't leak.
    watchdog.record_invocation_start()
    assert watchdog.last_deferred_kind is None
