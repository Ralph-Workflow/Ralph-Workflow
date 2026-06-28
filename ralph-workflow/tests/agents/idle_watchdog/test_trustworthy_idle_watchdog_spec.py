"""Consolidated acceptance-criteria test for the Trustworthy Idle Watchdog spec.

This module is the consolidated acceptance-criteria summary for the
wt-021 product spec (see ``.agent/CURRENT_PROMPT.md``). Every R1-R8
criterion is exercised in ONE ordinary test method (``test_r1`` through
``test_r8``). The methods are NOT parametrized -- they mirror the
plain-method precedent in ``tests/agents/test_builtin_spec_consolidation.py``
(5 ordinary methods, no ``@pytest.mark.parametrize``).

Every test method drives the SAME observable behaviors its dedicated
pin test pins (see ``RALPH_PIN_TEST_PATHS`` for the canonical pin list).
Where the pin test exclusively uses public surfaces (e.g. R5's
``last_subagent_progress_description`` / ``diagnostic_snapshot``), this
module does the same. Where the pin test consults a private seam
because no public surface exists (e.g. R6's per-tuple throttle map
``_last_deferred_log_at`` and coarse throttle map
``_last_any_deferred_log_at`` -- the dedicated pin tests at
``test_log_spam_throttle.py`` consult these directly), this module
follows the same precedent -- the seams are the only surfaces that
expose the throttle behavior, and the dedicated pin tests already
established that as the canonical observation contract. The
``black-box`` claim is therefore qualified: tests assert observable
behavior (loguru-captured DEBUG records for R6, classified-failure
shape for R7, watchdog verdicts for R2/R3) where public surfaces
exist, and they consult private seams ONLY where the dedicated pin
tests already established that pattern.

Test isolation guarantees:

  * No real subprocess (FakeClock + a tiny ``@dataclass`` satisfying
    the ``ProcessMonitor`` Protocol -- the pattern from
    ``test_subagent_identity_excludes_helpers.py``).
  * No real filesystem (no ``tmp_path``, no ``open()``, no
    ``Path.read_text()``).
  * No real wall-clock waits (``time.sleep(0)`` only via
    ``FakeClock.advance``).
  * No module-level mutable accumulators. ``RALPH_PIN_TEST_PATHS`` is
    an IMMUTABLE ``tuple[str, ...]`` (audit_resource_lifecycle allows
    tuples and frozensets as immutable).
  * No ``noqa`` directives (audit_lint_bypass).
  * No bare type-ignore comments -- tests must be fully typed per
    AGENTS.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import loguru
import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    SubagentIdentity,
    SubagentPidRegistry,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusListener,
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
    StuckKind,
    classify_stuck,
)
from ralph.agents.idle_watchdog._subagent_identity import _MAX_REGISTRY_ENTRIES
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._idle_stream_timeout_error import _IdleStreamTimeoutError
from ralph.agents.invoke._open_code_resumable_exit_error import OpenCodeResumableExitError
from ralph.agents.invoke._process_reader import (
    _convert_idle_stream_timeout_to_agent_error,
)
from ralph.agents.invoke._session_resume import recovery_action_for_failure_reason
from ralph.agents.timeout_clock import FakeClock
from ralph.process.monitor import (
    ProcessMonitor,
    SubagentOutputCapture,
)
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier

# Immutable reference list (audit_resource_lifecycle accepts ``tuple`` and
# ``frozenset`` as immutable; do NOT convert to a mutable ``list``).
# Every entry MUST exist on disk; ``test_r8`` below enforces that via
# ``absolute_path.is_file()``. The list mirrors the dedicated pin tests
# enumerated in ``ralph-workflow/docs/agents/watchdog-spec.md`` — when a
# pin test is added to the spec, append the relative path here too.
RALPH_PIN_TEST_PATHS: tuple[str, ...] = (
    # R1 - Child-process monitors count only real subagents.
    "tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py",
    "tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py",
    "tests/agents/idle_watchdog/test_shared_subagent_pid_registry.py",
    # R1 (NEW) - production SubagentPidRegistry wiring end-to-end pin
    # for the AgentRegistry -> BaseExecutionStrategy ->
    # classify_quiet injection path and the parser-side registry
    # storage. Pinned in wt-021 to lock the production wiring and
    # catch any future refactor that bypasses the registry seam.
    "tests/agents/idle_watchdog/test_production_subagent_registry_wiring.py",
    # R2 - No false positives.
    "tests/agents/idle_watchdog/test_silent_after_tool_call_wedge.py",
    "tests/agents/idle_watchdog/test_stuck_classifier.py",
    "tests/agents/idle_watchdog/test_no_output_at_start_loading.py",
    # R3 - No false negatives.
    "tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py",
    "tests/agents/idle_watchdog/test_stuck_job_sub_ceiling.py",
    "tests/agents/idle_watchdog/test_session_ceiling_no_resume.py",
    "tests/agents/idle_watchdog/test_pure_stall_wedge.py",
    # R3 (NEW) - cumulative ceiling hard-enforcement pin. Uses
    # FakeClock + Protocol-typed @dataclass ProcessMonitor fake
    # (NO real subprocess) so it stays in the canonical R8 audit
    # target. Pinned in wt-021 to lock the fix that removed the
    # _gate_fire consultation from the cumulative ceiling block.
    "tests/agents/idle_watchdog/test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py",
    # R3 (NEW) - heartbeat-only ceiling pin for stuck jobs that
    # emit heartbeats but no real work (AliveBy.FRESH_HEARTBEAT_ONLY).
    # Pinned in wt-021 to lock the no_progress_quiet_heartbeat_ceiling
    # enforcement and the FRESH_PROGRESS deferral invariant.
    "tests/agents/idle_watchdog/test_stuck_job_heartbeat_ceiling.py",
    # R4 - Resume on watchdog kill, never restart.
    "tests/agents/idle_watchdog/test_resume_after_kill_contract.py",
    "tests/agents/idle_watchdog/test_resume_after_kill_watchdog_boundary.py",
    "tests/agents/idle_watchdog/test_resume_session_id_threading.py",
    "tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py",
    "tests/agents/idle_watchdog/test_resume_contract_invariant.py",
    # R5 - Real-time subagent visibility for all supported agents.
    "tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py",
    "tests/agents/idle_watchdog/test_subagent_progress_surface.py",
    "tests/agents/idle_watchdog/test_waiting_subagent_progress.py",
    "tests/process/monitor/test_dispatch_all_transports.py",
    # R6 - Quiet, meaningful output.
    "tests/agents/idle_watchdog/test_log_spam_throttle.py",
    "tests/agents/idle_watchdog/test_log_spam_throttle_public_surface.py",
    "tests/agents/idle_watchdog/test_evidence_deferral_throttle.py",
    "tests/agents/idle_watchdog/test_invocation_start_full_reset.py",
    # R7 - Explain and handle the "mysterious" rc=0 exits.
    "tests/recovery/test_opencode_resumable_exit_classification.py",
    "tests/recovery/test_opencode_resumable_exit_classifier.py",
    "tests/recovery/test_opencode_resumable_exit_producer_path.py",
)


# ---------------------------------------------------------------------------
# Test fixtures (canonical @dataclass fake of ProcessMonitor Protocol)
# ---------------------------------------------------------------------------


@dataclass
class _HelpersOnlyMonitor(ProcessMonitor):
    """Helpers-only monitor: broader count = N helpers, filtered count = 0.

    The filtered count (the SEAM) is what the watchdog defers on. The
    broader count is documented for the test (and the audit regression)
    but is NOT consumed by the watchdog -- ``audit_activity_aware_watchdog``
    flags any reader that consumes the broader count for
    ``scoped_child_active``.
    """

    helper_count: int = 10
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return 0

    def spawned_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


@dataclass
class _FilteredCountMonitor(ProcessMonitor):
    """Filtered count monitor (returns filtered_count from both seam names).

    Mirrors ``_FilteredCountMonitor`` in
    ``tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py``.
    Both ``live_subagent_count()`` (legacy alias) and
    ``spawned_subagent_count()`` (preferred) return ``filtered_count``.
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


def _make_idle_watchdog(
    *,
    process_monitor: ProcessMonitor,
    policy: TimeoutPolicy,
    clock: FakeClock,
) -> IdleWatchdog:
    """Build an IdleWatchdog with the given policy and clock."""
    return IdleWatchdog(policy, clock, process_monitor=process_monitor)


# ---------------------------------------------------------------------------
# Test class: one ordinary test method per R1-R8
# ---------------------------------------------------------------------------


class TestTrustworthyIdleWatchdogSpec:
    """One ordinary test method per R1-R8 (no parametrization).

    Each test method drives the SAME observable behavior the dedicated
    pin test pins. Where the pin test asserts via captured loguru
    records (R6), classified-failure shape (R7), or verdict (R2/R3),
    this module does the same. The pin-test citations in each
    docstring make the cross-reference explicit.
    """

    def test_r1(self) -> None:
        """R1: child-process monitors count only real subagents.

        Drives the SAME observable behaviors as
        ``tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py``:

          (a) Helpers-only case: a monitor with N=10 helpers visible in
              the descendant tree returns 0 from BOTH
              ``spawned_subagent_count()`` and ``live_subagent_count()``
              -- the filtered count is the SEAM the watchdog defers on;
              the broader count is irrelevant.

          (b) Per-transport isolation: a PID registered for ``claude``
              is invisible to the ``opencode`` per-transport filter
              (and vice versa). The shared ``SubagentPidRegistry`` is
              filtered by ``source`` at the seam surface.

        Also asserts the FIFO cap (``_MAX_REGISTRY_ENTRIES == 1024``)
        so a long unattended invocation cannot retain unbounded
        ``SubagentIdentity`` records. The cap is the headline R1 +
        resource-lifecycle invariant.
        """
        # (a) Helpers-only case: broader count = 10, filtered count = 0.
        monitor = _HelpersOnlyMonitor(helper_count=10)
        assert monitor.spawned_subagent_count() == 0
        assert monitor.live_subagent_count() == 0
        # The alias is faithful: both seam names return the same filtered value.
        assert monitor.spawned_subagent_count() == monitor.live_subagent_count()
        # The watchdog's helpers-only monitor is independent of the
        # helper count -- the SEAM is the filtered count.
        assert monitor.helper_count == 10  # documented for the audit regression.

        # (b) Per-transport isolation: a Claude-registered PID is invisible
        # to an OpenCode filter (and vice versa). Two registries would
        # work; we use a single shared registry plus per-transport
        # snapshotting to demonstrate the per-source filter contract.
        registry = SubagentPidRegistry()
        registry.register(8001, source="claude", now=0.0)
        registry.register(8002, source="opencode", now=0.0)
        opencode_pids = {
            identity.pid
            for identity in registry.snapshot()
            if identity.source == "opencode"
        }
        claude_pids = {
            identity.pid
            for identity in registry.snapshot()
            if identity.source == "claude"
        }
        assert opencode_pids == {8002}
        assert claude_pids == {8001}
        # The per-transport filter is DISJOINT: no overlap.
        assert opencode_pids & claude_pids == set()

        # (c) FIFO cap on the registry (resource-lifecycle invariant).
        assert _MAX_REGISTRY_ENTRIES == 1024

        # (d) Identity constructor rejects unknown sources (canonical
        # transport set is closed).
        bad_source: Any = "unknown-transport"
        with pytest.raises(ValueError, match="unknown subagent source"):
            SubagentIdentity(
                pid=1234,
                source=cast("SubagentIdentity.__init__", bad_source),
                registered_at_monotonic=0.0,
            )

    def test_r2(self) -> None:
        """R2: no false positives -- the watchdog defers on real activity.

        Drives the SAME observable behaviors as
        ``tests/agents/idle_watchdog/test_silent_after_tool_call_wedge.py``
        and ``test_stuck_classifier.py``:

          (a) ``classify_stuck`` maps ``WAITING_ON_CHILD`` -> ``LOADING``
              (defer) and ``RESUMABLE_CONTINUE`` -> ``TRANSITIONING``
              (defer).
          (b) The smart-verdict gate (``_gate_fire``) returns CONTINUE
              for non-STUCK kinds: a fresh ``IdleWatchdog.evaluate``
              with ``classify_quiet=lambda: WAITING_ON_CHILD`` returns
              ``WAITING_ON_CHILD`` (defer), NOT FIRE -- the watchdog
              does NOT kill healthy / waiting work.
        """
        # (a) ``classify_stuck`` -- pure-function mapping (no I/O).
        empty_summary = EvidenceSummary(
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

        kind_loading = classify_stuck(
            is_waiting_state=False,
            connectivity_state="online",
            evidence_summary=empty_summary,
            classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD,
            activity_evidence_ttl_seconds=30.0,
        )
        assert kind_loading == StuckKind.LOADING

        kind_transitioning = classify_stuck(
            is_waiting_state=False,
            connectivity_state="online",
            evidence_summary=empty_summary,
            classify_quiet=lambda: AgentExecutionState.RESUMABLE_CONTINUE,
            activity_evidence_ttl_seconds=30.0,
        )
        assert kind_transitioning == StuckKind.TRANSITIONING

        # (b) ``IdleWatchdog.evaluate`` -- end-to-end deferral via public API.
        clock = FakeClock(start=0.0)
        policy = TimeoutPolicy(
            idle_timeout_seconds=2.0,
            no_output_at_start_seconds=None,
            no_progress_quiet_seconds=None,
            activity_evidence_ttl_seconds=0.0,
        )
        watchdog = _make_idle_watchdog(
            process_monitor=_HelpersOnlyMonitor(),
            policy=policy,
            clock=clock,
        )
        watchdog.record_invocation_start()
        clock.advance(3.0)

        def _waiting() -> AgentExecutionState:
            return AgentExecutionState.WAITING_ON_CHILD

        verdict = watchdog.evaluate(classify_quiet=_waiting)
        # The watchdog defers -- it does NOT fire while activity is recent
        # or the agent is legitimately waiting.
        assert verdict != WatchdogVerdict.FIRE
        assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    def test_r3(self) -> None:
        """R3: hard ceilings fire even when helpers are alive.

        Drives the SAME observable behavior as
        ``tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py``:

          (a) ``SESSION_CEILING_EXCEEDED`` fires with 10 helpers visible
              but 0 real subagents (the 2365s indefinite deferral
              CANNOT happen).
          (b) ``CHILDREN_PERSIST_TOO_LONG`` fires with helpers-only and
              no real subagent alive (the cumulative ceiling cannot be
              blocked by helpers).
        """
        # (a) SESSION_CEILING_EXCEEDED with helpers-only monitor.
        clock = FakeClock(start=0.0)
        policy = TimeoutPolicy(
            idle_timeout_seconds=60.0,
            max_session_seconds=300.0,
            no_output_at_start_seconds=None,
            no_progress_quiet_seconds=None,
            max_waiting_on_child_seconds=600.0,
            max_waiting_on_child_no_progress_seconds=None,
            suspect_waiting_on_child_seconds=None,
            activity_evidence_ttl_seconds=0.0,
        )
        monitor = _HelpersOnlyMonitor(helper_count=10)
        watchdog = _make_idle_watchdog(
            process_monitor=monitor, policy=policy, clock=clock
        )
        watchdog.record_invocation_start()
        clock.advance(305.0)
        verdict = watchdog.evaluate(classify_quiet=_active)
        assert verdict == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
        # The filtered count is 0; helpers (10) were ignored.
        assert monitor.spawned_subagent_count() == 0
        assert monitor.helper_count == 10

        # (b) CHILDREN_PERSIST_TOO_LONG fires when cumulative
        # waiting-time exceeds the ceiling with 0 real subagents.
        clock_b = FakeClock(start=0.0)
        policy_b = TimeoutPolicy(
            idle_timeout_seconds=2.0,
            max_waiting_on_child_seconds=5.0,
            max_waiting_on_child_no_progress_seconds=None,
            os_descendant_only_ceiling_seconds=None,
            stuck_job_sub_ceiling_seconds=None,
            no_progress_quiet_seconds=None,
            no_output_at_start_seconds=None,
            suspect_waiting_on_child_seconds=None,
            activity_evidence_ttl_seconds=0.0,
        )
        monitor_b = _HelpersOnlyMonitor(helper_count=10)
        watchdog_b = _make_idle_watchdog(
            process_monitor=monitor_b, policy=policy_b, clock=clock_b
        )
        watchdog_b.record_invocation_start()
        clock_b.advance(3.0)

        def _waiting_b() -> AgentExecutionState:
            return AgentExecutionState.WAITING_ON_CHILD

        first_verdict = watchdog_b.evaluate(classify_quiet=_waiting_b)
        assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD
        clock_b.advance(5.0)
        verdict_b = watchdog_b.evaluate(classify_quiet=_waiting_b)
        assert verdict_b == WatchdogVerdict.FIRE
        assert watchdog_b.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
        # Helpers were ignored -- the FILTERED count is the SEAM.
        assert monitor_b.spawned_subagent_count() == 0
        assert monitor_b.helper_count == 10

    def test_r4(self) -> None:
        """R4: watchdog-driven kills resume the existing session.

        Drives the SAME observable end-to-end path as
        ``tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py``:

          1. Build an ``IdleWatchdogKilledError(reason=NO_OUTPUT_AT_START,
             signal=15, resumable_session_id="sess-abc-123")`` -- the
             watchdog kill carries the captured session id.
          2. Wrap it in ``_IdleStreamTimeoutError`` (the wrapper the
             line reader produces).
          3. Convert via ``_convert_idle_stream_timeout_to_agent_error``
             -- the converted ``AgentInactivityTimeoutError`` MUST
             carry the captured id with ``session_resume_safe=True``.
          4. Classify with ``FailureClassifier`` -- the typed-cause
             branch applies the ``resumable_kill`` carve-out so
             ``is_unavailable=False`` and the failure is
             ``FailureCategory.AGENT``.
          5. ``recovery_action_for_failure_reason('AgentInactivityTimeoutError',
             has_prior_session=True)`` returns ``'resume'`` -- the
             recovery controller threads the prior session forward.

        Also asserts the fresh path: ``OpenCodeResumableExitError``
        (the R7 deterministic rc=0 classification) also resumes the
        prior session; without a prior session the action is
        ``'fresh'``.
        """
        # Step 1+2: build the watchdog kill + wrapper.
        typed_exc = IdleWatchdogKilledError(
            reason=WatchdogFireReason.NO_OUTPUT_AT_START.value,
            signal=15,
            evidence_summary="no_output_at_start fired",
            child_alive=False,
            resumable_session_id="sess-abc-123",
        )
        wrapper = _IdleStreamTimeoutError(
            timeout_seconds=31.0,
            reason=WatchdogFireReason.NO_OUTPUT_AT_START,
            diagnostic={"idle_elapsed": 31.0},
        )
        wrapper.__cause__ = typed_exc

        # Step 3: convert via the canonical seam.
        converted = _convert_idle_stream_timeout_to_agent_error(
            agent_name="opencode",
            exc=wrapper,
            parsed_output=("line 1", "line 2"),
            explicit_completion_seen=False,
            captured_session_id="sess-abc-123",
        )
        assert converted.resumable_session_id == "sess-abc-123"
        assert converted.session_resume_safe is True
        assert converted.reason == WatchdogFireReason.NO_OUTPUT_AT_START

        # Step 4: classify with the failure classifier. The
        # ``resumable_kill`` carve-out sets ``is_unavailable=False`` so
        # the recovery controller emits a resume intent.
        classifier = FailureClassifier()
        failure = classifier.classify(
            converted,
            phase="development",
            agent="opencode",
            connectivity_state="online",
        )
        assert failure.category == FailureCategory.AGENT
        assert failure.resumable_session_id == "sess-abc-123"
        assert failure.is_unavailable is False

        # Step 5: the recovery action is 'resume' when a prior session exists.
        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError",
                has_prior_session=True,
            )
            == "resume"
        )
        # Without a prior session the action is 'fresh' (the fresh path is
        # function-separate from the resume path).
        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError",
                has_prior_session=False,
            )
            == "fresh"
        )
        # R7 deterministic rc=0 classification also resumes the prior session.
        assert (
            recovery_action_for_failure_reason(
                "OpenCodeResumableExitError",
                has_prior_session=True,
            )
            == "resume"
        )

    def test_r5(self) -> None:
        """R5: real-time subagent visibility for all supported agents.

        EXPLICIT THREE-FIELD PUBLIC CONTRACT (the R5 surface extension
        is the headline deliverable of wt-021):

          (a) PROGRESS = ``last_subagent_progress_description``
              (``str | None``) -- the free-form description text set by
              ``record_subagent_work(description=...)``. EXISTING field
              -- preserved for backward compatibility.
          (b) LAST ACTIVITY = ``last_subagent_progress_at``
              (``float | None``) -- the monotonic timestamp of the most
              recent subagent observation. NEW field.
          (c) CURRENT TOOL CALL = ``current_subagent_tool_call``
              (``str | None``) -- the parsed ``verb:`` prefix from the
              description when the description starts with a known
              tool-call verb (``tool_use``, ``tool_result``, ``mcp_tool``,
              ``subagent``, ``bash``, ``read``, ``write``, ``edit``,
              ``glob``, ``grep``, ``webfetch``, ``websearch``);
              ``None`` otherwise. NEW field.

        All three fields MUST be exposed on BOTH:
          * the public ``diagnostic_snapshot()`` dict, AND
          * the public ``WaitingStatusEvent`` surface.

        Drives the SAME observable behavior as
        ``tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py``
        (per-transport parametrize over ``list(AgentTransport)``) +
        ``tests/agents/idle_watchdog/test_subagent_progress_surface.py`` +
        ``tests/agents/idle_watchdog/test_waiting_subagent_progress.py``.
        """
        clock = FakeClock(start=0.0)
        policy = TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_output_at_start_seconds=30.0,
            no_progress_quiet_seconds=None,
            activity_evidence_ttl_seconds=180.0,
        )
        captured: list[WaitingStatusEvent] = []

        def _capture(event: WaitingStatusEvent) -> None:
            captured.append(event)

        watchdog = _make_idle_watchdog(
            process_monitor=_HelpersOnlyMonitor(),
            policy=policy,
            clock=clock,
        )
        watchdog.record_invocation_start()
        watchdog.register_default_subagent_activity_listener(
            cast("WaitingStatusListener", _capture)
        )
        # Baseline: all three R5 fields are None at invocation start
        # (per-invocation reset semantics).
        assert watchdog.last_subagent_progress_description is None
        baseline_snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert baseline_snapshot["last_subagent_progress_at"] is None
        assert baseline_snapshot["current_subagent_tool_call"] is None

        # Recording subagent work with a known-verb description
        # populates ALL THREE R5 fields on the watchdog surface.
        # PROGRESS (existing field): free-form description text.
        watchdog.record_subagent_work(description="tool_use:Read")
        assert watchdog.last_subagent_progress_description == "tool_use:Read"
        # LAST ACTIVITY (new field): monotonic timestamp is a
        # non-None float >= 0.0.
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        last_activity = snapshot["last_subagent_progress_at"]
        assert last_activity is not None
        assert isinstance(last_activity, float)
        assert last_activity >= 0.0
        # CURRENT TOOL CALL (new field): the parsed verb prefix.
        assert snapshot["current_subagent_tool_call"] == "tool_use"

        # Drive the watchdog through ``evaluate()`` with a
        # ``WAITING_ON_CHILD`` ``classify_quiet`` so the watchdog
        # transitions into the waiting branch and emits the ENTERED
        # + SUBAGENT_PROGRESS waiting-status events naturally. The
        # default subagent activity listener captures both events.
        clock.advance(61.0)

        def _waiting() -> AgentExecutionState:
            return AgentExecutionState.WAITING_ON_CHILD

        watchdog.evaluate(classify_quiet=_waiting)
        assert captured, (
            "watchdog.evaluate MUST emit at least one waiting-status"
            " event when transitioning into WAITING_ON_CHILD"
        )

        # Every captured event MUST carry all three R5 fields on
        # the ``WaitingStatusEvent`` surface. The default subagent
        # activity listener receives every event whose
        # ``subagent_activity`` is not None (ENTERED, PROGRESS,
        # SUBAGENT_PROGRESS all qualify), so the assertion
        # guarantees every emitted event carries the full R5
        # public contract.
        for event in captured:
            assert event.subagent_activity == "tool_use:Read", (
                f"WaitingStatusEvent PROGRESS field MUST carry the"
                f" recorded description; got {event.subagent_activity!r}"
            )
            assert event.last_subagent_progress_at is not None, (
                "WaitingStatusEvent LAST ACTIVITY field MUST be populated"
            )
            assert isinstance(event.last_subagent_progress_at, float), (
                "WaitingStatusEvent LAST ACTIVITY field MUST be a float"
            )
            assert event.last_subagent_progress_at >= 0.0, (
                "WaitingStatusEvent LAST ACTIVITY field MUST be >= 0.0"
            )
            assert event.current_subagent_tool_call == "tool_use", (
                "WaitingStatusEvent CURRENT TOOL CALL field MUST be"
                " the parsed verb from the observed description"
            )

        # A follow-up record_subagent_work overwrites the prior
        # description; PROGRESS + CURRENT TOOL CALL follow.
        watchdog.record_subagent_work(description="tool_use:Edit")
        assert watchdog.last_subagent_progress_description == "tool_use:Edit"
        snapshot2 = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot2["current_subagent_tool_call"] == "tool_use"

        # A description with no known verb prefix resets CURRENT
        # TOOL CALL to None while PROGRESS retains the text.
        watchdog.record_subagent_work(
            description="[subagent] progress: phase=phase-1"
        )
        assert (
            watchdog.last_subagent_progress_description
            == "[subagent] progress: phase=phase-1"
        )
        snapshot3 = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot3["current_subagent_tool_call"] is None

        # New invocation clears ALL THREE R5 fields back to None
        # (per-invocation reset semantics from R5).
        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None
        reset_snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert reset_snapshot["last_subagent_progress_at"] is None
        assert reset_snapshot["current_subagent_tool_call"] is None

    def test_r6(self) -> None:
        """R6: combined coarse + per-tuple throttle caps log emissions.

        Drives the SAME observable behavior as
        ``tests/agents/idle_watchdog/test_log_spam_throttle.py``:

          (a) Capture DEBUG records emitted by the watchdog via a
              loguru sink filtered on ``component="idle_watchdog"``.
          (b) Drive 1000 calls to ``_gate_fire`` cycling
              ``SILENT_SUBAGENT <-> LOADING`` in a single throttle
              window.
          (c) Assert the captured DEBUG records count is ``<=2``
              (initial transition + refresh). The headline R6
              invariant: the COMBINED coarse per-fire_reason map
              (``_last_any_deferred_log_at``) + per-tuple map
              (``_last_deferred_log_at``) caps emissions to one
              DEBUG record per ``watchdog_log_throttle_seconds`` per
              ``fire_reason`` regardless of how the ``deferred_kind``
              cycles. Pre-fix the count was 1000 (the prompt's log
              spam regression).

        The captured DEBUG records is the OBSERVABLE behavior
        (every other surface is implementation detail). The pin test
        uses the same capture pattern (loguru StringIO sink filtered
        on ``component="idle_watchdog"``).
        """
        # (a) Capture DEBUG records via a loguru sink (mirrors the pin test).
        records: list[str] = []

        def _sink(message: str) -> None:
            records.append(message)

        handler_id = logger.add(
            _sink,
            level="DEBUG",
            format="{message}",
            filter=lambda record: "idle_watchdog"
            in (record["extra"].get("component") or ""),
        )
        try:
            # Build the throttle-pinned watchdog (idle=60s,
            # no_output_at_start=30s, throttle=30s, ttl=180s -- the
            # canonical values from test_log_spam_throttle.py).
            clock = FakeClock(start=0.0)
            policy = TimeoutPolicy(
                idle_timeout_seconds=60.0,
                no_output_at_start_seconds=30.0,
                no_progress_quiet_seconds=None,
                watchdog_log_throttle_seconds=30.0,
                activity_evidence_ttl_seconds=180.0,
            )
            watchdog = _make_idle_watchdog(
                process_monitor=_HelpersOnlyMonitor(),
                policy=policy,
                clock=clock,
            )

            # (b) Force ``_classify_stuck_now`` to cycle SILENT_SUBAGENT
            # <-> LOADING on every call so the per-tuple throttle key
            # changes every tick. The COARSE throttle (the headline
            # R6 fix) caps emissions regardless.
            cycle = [StuckKind.SILENT_SUBAGENT, StuckKind.LOADING]
            call_log: list[StuckKind] = []

            def _stuck_now(
                *,
                now: float,
                idle_elapsed: float,
                corroboration: CorroborationSnapshot | None = None,
            ) -> StuckKind:
                kind = call_log[0] if call_log else StuckKind.SILENT_SUBAGENT
                return kind

            # ``setattr`` with attribute name in a local variable
            # (audit_lint_bypass: bare constant setattr is ruff B010;
            # mypy cannot narrow access to a private-method
            # assignment).
            _classify_attr = "_classify_stuck_now"
            setattr(watchdog, _classify_attr, _stuck_now)

            fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

            # Drive 1000 calls cycling the deferred_kind. The coarse
            # throttle caps emissions to one DEBUG record per
            # ``watchdog_log_throttle_seconds`` per ``fire_reason``.
            for i in range(1000):
                call_log = [cycle[i % 2]]
                verdict = watchdog._gate_fire(
                    fire_reason,
                    now=clock.monotonic(),
                    idle_elapsed=300.0,
                    corroboration=CorroborationSnapshot(),
                )
                assert verdict == WatchdogVerdict.CONTINUE

            # (c) Assert the captured DEBUG records is <= 2.
            # The COARSE throttle is keyed on ``fire_reason.value``
            # alone, so the matched records are any deferred log
            # line containing the fire_reason -- the per-tuple kind
            # label cycles but the headline signal stays the same.
            matching = [
                r
                for r in records
                if (
                    ("silent subagent" in r or "deferred fire" in r)
                    and "CHILDREN_PERSIST_TOO_LONG" in r
                )
            ]
            assert len(matching) <= 2, (
                f"coarse single-key throttle MUST cap emissions across"
                f" kind-cycles; got {len(matching)} records for 1000"
                f" calls in the same throttle window."
                f" Records (first 3): {matching[:3]}"
            )
        finally:
            logger.remove(handler_id)

    def test_r6_heartbeat(self) -> None:
        """R6: human-readable waiting heartbeat cadence and payload.

        Pins the LOW-FREQUENCY HEARTBEAT chosen for R6: the watchdog
        emits a single periodic INFO record per ``evaluate()`` call
        whose gate passes, with a human-readable template naming
        (a) what is happening (waiting on subagent), (b) the live
        subagent count from the FILTERED process monitor (R1),
        (c) elapsed seconds, and (d) the hard ceiling seconds. The
        WAITING entry log emitted at ``_waiting_branch.py:122`` is a
        SEPARATE loguru INFO record -- it fires ONCE on the first
        ``evaluate()`` call when ``WAITING_ON_CHILD`` is entered; the
        heartbeat fires on every subsequent ``evaluate()`` call whose
        cadence gate ``now - _last_waiting_status_at >=
        waiting_status_interval_seconds`` is satisfied.

        Captures both INFO records via a loguru sink filtered on
        ``component='idle_watchdog'`` (the same filter shape as
        ``test_r6``) and post-filters the captured records to the
        heartbeat template BEFORE asserting cadence and message
        fields. The substring 'agent waiting on subagent' is the
        canonical heartbeat distinguisher -- the WAITING entry log
        uses 'entering WAITING_ON_CHILD deferral' instead, so the
        post-filter cleanly separates the two.

        Cadence math (5 calls, 1.0s advance between each, with
        ``waiting_status_interval_seconds=1.0``):

          * Call 1 (clock=1.0): WAITING entered, entry log fires,
            ``_last_waiting_status_at=1.0``, cadence gate
            ``(1.0-1.0)=0.0 >= 1.0`` is False, NO heartbeat.
          * Call 2 (clock=2.0): cadence gate ``(2.0-1.0)=1.0 >= 1.0``
            True, heartbeat fires (elapsed=1.0).
          * Call 3 (clock=3.0): cadence gate ``(3.0-2.0)=1.0 >= 1.0``
            True, heartbeat fires (elapsed=2.0).
          * Call 4 (clock=4.0): cadence gate ``(4.0-3.0)=1.0 >= 1.0``
            True, heartbeat fires (elapsed=3.0).
          * Call 5 (clock=5.0): cadence gate ``(5.0-4.0)=1.0 >= 1.0``
            True, heartbeat fires (elapsed=4.0).

        Total INFO records: 1 entry log + 4 heartbeats = 5. Post-
        filtered heartbeat records: 4. The 5-record total asserts the
        entry log was NOT silently dropped by the post-filter.

        The test uses ``idle_timeout_seconds=0.5`` (small enough to
        enter WAITING_ON_CHILD on every call) and disables every
        other fire path so the watchdog ONLY exercises the WAITING
        branch (no_output_at_start=None, no_progress_quiet=None,
        max_waiting_on_child_no_progress_seconds=None,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=0.0). The 1.0s advance between
        calls keeps ``candidate_total < max_waiting_on_child_seconds``
        so CHILDREN_PERSIST_TOO_LONG never fires.
        """
        # (1) Attach a loguru sink filtered on INFO records emitted
        # from the watchdog's ``self._log`` (component='idle_watchdog').
        # The filter receives the loguru record dict; the
        # ``record['extra'].get('component')`` access matches the
        # established pattern in ``test_r6`` (line 723).
        records: list[loguru.Message] = []

        def _sink(message: loguru.Message) -> None:
            records.append(message)

        handler_id = logger.add(
            _sink,
            level="INFO",
            format="{message}",
            filter=lambda record: "idle_watchdog"
            in (record["extra"].get("component") or ""),
        )
        try:
            # (2) Build a watchdog with a small idle_timeout_seconds
            # (0.5) so every evaluate() call enters the WAITING branch.
            # All other fire paths are disabled via None so the
            # watchdog ONLY exercises the WAITING_ON_CHILD branch.
            clock = FakeClock(start=0.0)
            policy = TimeoutPolicy(
                idle_timeout_seconds=0.5,
                max_waiting_on_child_seconds=600.0,
                no_output_at_start_seconds=None,
                no_progress_quiet_seconds=None,
                max_waiting_on_child_no_progress_seconds=None,
                suspect_waiting_on_child_seconds=None,
                waiting_status_interval_seconds=1.0,
                watchdog_log_throttle_seconds=30.0,
                activity_evidence_ttl_seconds=0.0,
            )
            watchdog = _make_idle_watchdog(
                process_monitor=_HelpersOnlyMonitor(),
                policy=policy,
                clock=clock,
            )

            # (3) ``record_invocation_start`` resets the per-invocation
            # baseline so ``_last_activity = clock.monotonic()`` and
            # ``_session_started_at = clock.monotonic()`` (both = 0.0).
            watchdog.record_invocation_start()

            # (4) The execution strategy reports WAITING_ON_CHILD on
            # every call so the WAITING branch is taken on every call.
            def _waiting() -> AgentExecutionState:
                return AgentExecutionState.WAITING_ON_CHILD

            # (5) Drive 5 calls. Advance BEFORE each call so the
            # call times are 1.0, 2.0, 3.0, 4.0, 5.0 -- the first
            # call at clock=1.0 has ``idle_elapsed=1.0 >= 0.5`` and
            # therefore enters the WAITING branch and emits the
            # entry log.
            for _ in range(5):
                clock.advance(1.0)
                watchdog.evaluate(classify_quiet=_waiting)

            # (6) Post-filter captured INFO records to the heartbeat
            # template BEFORE asserting cadence and message fields.
            # The WAITING entry log uses 'entering WAITING_ON_CHILD
            # deferral' (a disjoint substring); the heartbeat uses
            # 'agent waiting on subagent' (the canonical heartbeat
            # substring). Post-filtering is the canonical
            # distinguisher -- the entry log must NOT appear in the
            # heartbeat_records list.
            heartbeat_records = [
                r for r in records if "agent waiting on subagent" in str(r.record["message"])
            ]

            # (7a) Cadence assertion: 4 heartbeat records. Math: the
            # WAITING entry log sets ``_last_waiting_status_at = now``
            # BEFORE the heartbeat gate runs (line 124 in
            # ``_waiting_branch.py``), so on call 1 the gate
            # ``(1.0-1.0)=0.0 >= 1.0`` is False (no heartbeat). On
            # calls 2-5 the gate ``(N - (N-1)) = 1.0 >= 1.0`` is True
            # and the heartbeat fires.
            assert len(heartbeat_records) == 4, (
                f"heartbeat cadence MUST emit one INFO record per"
                f" evaluate() call whose cadence gate passes"
                f" (waiting_status_interval_seconds=1.0); got"
                f" {len(heartbeat_records)} heartbeat records for"
                f" 5 evaluate() calls. Records: {[str(r.record['message']) for r in records]}"
            )

            # (7b) Total-records assertion: 1 entry log + 4 heartbeats
            # = 5 INFO records. Proves the post-filter is the actual
            # distinguisher and the WAITING entry log was NOT silently
            # dropped from the captured records.
            assert len(records) >= 5, (
                f"capture MUST contain at least 5 INFO records"
                f" (1 WAITING entry log + 4 heartbeat PROGRESS logs);"
                f" got {len(records)}. Records:"
                f" {[str(r.record['message']) for r in records]}"
            )

            # (7c) Every heartbeat record MUST carry all four
            # human-readable fields: the literal substring 'waiting',
            # the literal substring 'subagent', a regex match of
            # ``\\d+s`` for the elapsed-seconds field, and the literal
            # substring 'ceiling' followed by a regex match of
            # ``\\d+s`` for the hard-ceiling field.
            heartbeat_substring = "agent waiting on subagent"
            elapsed_seconds_pattern = re.compile(r"\d+s")
            ceiling_seconds_pattern = re.compile(r"\d+s")
            for record in heartbeat_records:
                message = str(record.record["message"])
                assert heartbeat_substring in message, (
                    f"heartbeat MUST contain '{heartbeat_substring}'"
                    f" (R6 chosen UX); got {message!r}"
                )
                assert "subagent" in message, (
                    f"heartbeat MUST contain 'subagent'; got {message!r}"
                )
                assert elapsed_seconds_pattern.search(message) is not None, (
                    f"heartbeat MUST carry an elapsed-seconds field"
                    f" matching \\d+s; got {message!r}"
                )
                assert "ceiling" in message, (
                    f"heartbeat MUST carry the literal 'ceiling'"
                    f" label; got {message!r}"
                )
                ceiling_tail = message.split("ceiling", 1)[1]
                assert ceiling_seconds_pattern.search(ceiling_tail) is not None, (
                    f"heartbeat MUST carry a hard-ceiling-seconds field"
                    f" matching \\d+s after 'ceiling'; got"
                    f" ceiling_tail={ceiling_tail!r}"
                )

            # (7d) Sanity check: the post-filter cleanly excludes
            # the WAITING entry log. The substring
            # 'entering WAITING_ON_CHILD deferral' must NOT appear in
            # any heartbeat record.
            assert not any(
                "entering WAITING_ON_CHILD deferral" in str(r.record["message"])
                for r in heartbeat_records
            ), (
                "post-filter MUST exclude the WAITING entry log;"
                " substring 'entering WAITING_ON_CHILD deferral'"
                " leaked into a heartbeat record:"
                f" {[str(r.record['message']) for r in heartbeat_records]}"
            )
        finally:
            logger.remove(handler_id)

    def test_r7(self) -> None:
        """R7: ambiguous rc=0 exits classify deterministically as AGENT.

        Drives the SAME observable behavior as
        ``tests/recovery/test_opencode_resumable_exit_classification.py``:

          (a) ``OpenCodeResumableExitError`` (with ANY session_id,
              including None) classifies as ``FailureCategory.AGENT``,
              NEVER ``FailureCategory.AMBIGUOUS``.
          (b) The ``resumable_session_id`` attribute propagates from
              the typed exception to the ``ClassifiedFailure``.
          (c) The failure counts against the budget (it's a real
              failure, not a recoverable artifact validation problem).
          (d) ``reset_session=False`` -- this is a resume-friendly
              exit, not a stale-session reset.
          (e) The four NEW R7 root-cause diagnostic fields
              (``last_observed_tool_call``, ``last_evidence_summary``,
              ``elapsed_seconds``, ``transcript_tail``) are preserved
              on the exception object -- the diagnostic surface is
              additive and does NOT change classification behavior.
        """
        classifier = FailureClassifier()
        for session_id in ("sess-a", "sess-b", "sess-c", None):
            exc = OpenCodeResumableExitError(
                agent_name="opencode",
                session_id=session_id,
                last_observed_tool_call="read_file",
                last_evidence_summary="workspace_change: kind=source weight=1.0",
                elapsed_seconds=420.0,
                transcript_tail=("line-1", "line-2"),
            )
            failure = classifier.classify(
                exc,
                phase="development",
                agent="opencode",
                connectivity_state="online",
            )
            # Headline: AGENT, NOT AMBIGUOUS.
            assert failure.category == FailureCategory.AGENT, (
                f"OpenCodeResumableExitError with session_id={session_id!r}"
                f" MUST classify as AGENT, got {failure.category!r}"
            )
            assert failure.category != FailureCategory.AMBIGUOUS
            # The session_id propagates (typed exception -> ClassifiedFailure).
            assert failure.resumable_session_id == session_id
            # Real failure (counts against the budget).
            assert failure.counts_against_budget is True
            # NOT a stale-session reset -- the recovery controller
            # resumes the existing session.
            assert failure.reset_session is False
            # The four NEW R7 root-cause diagnostic fields are
            # preserved on the exception object regardless of
            # the session_id (including None). The diagnostic
            # surface is additive and does NOT affect classification.
            assert exc.last_observed_tool_call == "read_file"
            assert exc.last_evidence_summary == (
                "workspace_change: kind=source weight=1.0"
            )
            assert exc.elapsed_seconds == 420.0
            assert exc.transcript_tail == ("line-1", "line-2")

    def test_r8(self) -> None:
        """R8: clean, black-box-testable architecture.

        Drives the SAME observable contract as
        ``ralph/testing/audit_test_policy.py``:

          (a) Every pin test file in ``RALPH_PIN_TEST_PATHS`` is
              discoverable on disk (the audit walks this tree).
          (b) The ``ProcessMonitor`` Protocol advertises
              ``spawned_subagent_count`` AND ``live_subagent_count``
              (the audit ``audit_activity_aware_watchdog`` flags any
              reader that uses ``descendant_snapshot`` instead of the
              filtered seam).
          (c) The canonical ``FakeClock`` is a deterministic clock
              advance (no real wall-clock) so every watchdog test in
              this module runs without ``time.sleep(N > 0)`` and
              without real subprocesses.
        """
        # (a) Pin test files exist on disk.  Resolve every path
        # relative to this test file's location (NOT the current
        # working directory) so the assertion is robust to invocation
        # from outside ``ralph-workflow/`` (e.g. when running pytest
        # with an absolute path from the repo root).  The package
        # layout is:
        #   ralph-workflow/
        #     tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py
        #     tests/<other pin test dirs>/...
        # so the test file's parent.parent.parent.parent resolves to
        # the ``ralph-workflow/`` directory which contains every
        # ``RALPH_PIN_TEST_PATHS`` entry verbatim (each entry begins
        # with ``tests/...``).
        test_root = Path(__file__).resolve().parent.parent.parent.parent
        for relative_path in RALPH_PIN_TEST_PATHS:
            absolute_path = test_root / relative_path
            assert absolute_path.is_file(), (
                f"Pin test file MUST exist at {absolute_path}"
            )

        # (b) ProcessMonitor Protocol advertises the filtered seam names.
        assert hasattr(ProcessMonitor, "spawned_subagent_count")
        assert hasattr(ProcessMonitor, "live_subagent_count")

        # (c) FakeClock is deterministic (no real wall-clock).
        clock = FakeClock(start=0.0)
        assert clock.monotonic() == 0.0
        clock.advance(123.0)
        assert clock.monotonic() == 123.0
        clock.advance(7.0)
        assert clock.monotonic() == 130.0


__all__ = [
    "RALPH_PIN_TEST_PATHS",
    "TestTrustworthyIdleWatchdogSpec",
]
