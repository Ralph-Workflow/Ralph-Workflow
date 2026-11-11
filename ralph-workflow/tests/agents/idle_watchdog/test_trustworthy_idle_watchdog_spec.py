from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot

import loguru
import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    MAX_SUBAGENT_REGISTRY_ENTRIES,
    ChannelEvidenceSummary,
    ChannelName,
    EvidenceSummary,
    EvidenceTier,
    IdleWatchdog,
    StuckKind,
    SubagentIdentity,
    SubagentPidRegistry,
    TimeoutPolicy,
    WaitingStatusEvent,
    WatchdogFireReason,
    WatchdogVerdict,
    classify_stuck,
)
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

RALPH_PIN_TEST_PATHS: tuple[str, ...] = (
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/process/monitor/test_dispatch_all_transports.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/agents/test_idle_watchdog.py",
    "tests/recovery/test_opencode_resumable_exit_classification.py",
    "tests/recovery/test_opencode_resumable_exit_classifier.py",
    "tests/recovery/test_opencode_resumable_exit_producer_path.py",
)


@dataclass
class _HelpersOnlyMonitor(ProcessMonitor):
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
    return IdleWatchdog(policy, clock, process_monitor=process_monitor)


class TestTrustworthyIdleWatchdogSpec:
    def test_r1(self) -> None:
        monitor = _HelpersOnlyMonitor(helper_count=10)
        assert monitor.spawned_subagent_count() == 0
        assert monitor.live_subagent_count() == 0
        assert monitor.spawned_subagent_count() == monitor.live_subagent_count()
        assert monitor.helper_count == 10

        registry = SubagentPidRegistry()
        registry.register(8001, source="claude", now=0.0)
        registry.register(8002, source="opencode", now=0.0)
        opencode_pids = {
            identity.pid for identity in registry.snapshot() if identity.source == "opencode"
        }
        claude_pids = {
            identity.pid for identity in registry.snapshot() if identity.source == "claude"
        }
        assert opencode_pids == {8002}
        assert claude_pids == {8001}
        assert opencode_pids & claude_pids == set()

        assert MAX_SUBAGENT_REGISTRY_ENTRIES == 1024

        bad_source: Any = "unknown-transport"
        with pytest.raises(ValueError, match="unknown subagent source"):
            SubagentIdentity(
                pid=1234,
                source=bad_source,
                registered_at_monotonic=0.0,
            )

    def test_r2(self) -> None:
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
        assert verdict != WatchdogVerdict.FIRE
        assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    def test_r3(self) -> None:
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
        watchdog = _make_idle_watchdog(process_monitor=monitor, policy=policy, clock=clock)
        watchdog.record_invocation_start()
        clock.advance(305.0)
        verdict = watchdog.evaluate(classify_quiet=_active)
        assert verdict == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
        assert monitor.spawned_subagent_count() == 0
        assert monitor.helper_count == 10

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
        watchdog_b = _make_idle_watchdog(process_monitor=monitor_b, policy=policy_b, clock=clock_b)
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
        assert monitor_b.spawned_subagent_count() == 0
        assert monitor_b.helper_count == 10

    def test_r4(self) -> None:
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

        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError",
                has_prior_session=True,
            )
            == "resume"
        )
        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError",
                has_prior_session=False,
            )
            == "fresh"
        )
        assert (
            recovery_action_for_failure_reason(
                "OpenCodeResumableExitError",
                has_prior_session=True,
            )
            == "resume"
        )

    def test_r5(self) -> None:
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
        watchdog.register_default_subagent_activity_listener(_capture)
        assert watchdog.last_subagent_progress_description is None
        baseline_snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert baseline_snapshot["last_subagent_progress_at"] is None
        assert baseline_snapshot["current_subagent_tool_call"] is None

        watchdog.record_subagent_work(description="tool_use:Read")
        assert watchdog.last_subagent_progress_description == "tool_use:Read"
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        last_activity = snapshot["last_subagent_progress_at"]
        assert last_activity is not None
        assert isinstance(last_activity, float)
        assert last_activity >= 0.0
        assert snapshot["current_subagent_tool_call"] == "tool_use"

        clock.advance(61.0)

        def _waiting() -> AgentExecutionState:
            return AgentExecutionState.WAITING_ON_CHILD

        watchdog.evaluate(classify_quiet=_waiting)
        assert captured, (
            "watchdog.evaluate MUST emit at least one waiting-status"
            " event when transitioning into WAITING_ON_CHILD"
        )

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

        watchdog.record_subagent_work(description="tool_use:Edit")
        assert watchdog.last_subagent_progress_description == "tool_use:Edit"
        snapshot2 = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot2["current_subagent_tool_call"] == "tool_use"

        watchdog.record_subagent_work(description="[subagent] progress: phase=phase-1")
        assert watchdog.last_subagent_progress_description == "[subagent] progress: phase=phase-1"
        snapshot3 = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot3["current_subagent_tool_call"] is None

        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None
        reset_snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert reset_snapshot["last_subagent_progress_at"] is None
        assert reset_snapshot["current_subagent_tool_call"] is None

    def test_r6(self) -> None:
        records: list[str] = []

        def _sink(message: str) -> None:
            records.append(message)

        handler_id = logger.add(
            _sink,
            level="DEBUG",
            format="{message}",
            filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
        )
        try:
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

            cycle = [StuckKind.DUPLICATE_KILL, StuckKind.LOADING]
            call_log: list[StuckKind] = []

            def _stuck_now(
                *,
                now: float,
                idle_elapsed: float,
                corroboration: CorroborationSnapshot | None = None,
            ) -> StuckKind:
                kind = call_log[0] if call_log else StuckKind.DUPLICATE_KILL
                return kind

            _classify_attr = "_classify_stuck_now"
            setattr(watchdog, _classify_attr, _stuck_now)

            fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

            for i in range(1000):
                call_log = [cycle[i % 2]]
                verdict = watchdog.evaluate_fire_gate(
                    fire_reason,
                    now=clock.monotonic(),
                    idle_elapsed=300.0,
                )
                assert verdict == WatchdogVerdict.CONTINUE

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
        records: list[loguru.Message] = []

        def _sink(message: loguru.Message) -> None:
            records.append(message)

        handler_id = logger.add(
            _sink,
            level="INFO",
            format="{message}",
            filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
        )
        try:
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

            watchdog.record_invocation_start()

            def _waiting() -> AgentExecutionState:
                return AgentExecutionState.WAITING_ON_CHILD

            for _ in range(5):
                clock.advance(1.0)
                watchdog.evaluate(classify_quiet=_waiting)

            heartbeat_records = [
                r for r in records if "agent waiting on subagent" in str(r.record["message"])
            ]

            assert len(heartbeat_records) == 4, (
                f"heartbeat cadence MUST emit one INFO record per"
                f" evaluate() call whose cadence gate passes"
                f" (waiting_status_interval_seconds=1.0); got"
                f" {len(heartbeat_records)} heartbeat records for"
                f" 5 evaluate() calls. Records: {[str(r.record['message']) for r in records]}"
            )

            assert len(records) >= 5, (
                f"capture MUST contain at least 5 INFO records"
                f" (1 WAITING entry log + 4 heartbeat PROGRESS logs);"
                f" got {len(records)}. Records:"
                f" {[str(r.record['message']) for r in records]}"
            )

            heartbeat_substring = "agent waiting on subagent"
            elapsed_seconds_pattern = re.compile(r"\d+s")
            ceiling_seconds_pattern = re.compile(r"\d+s")
            for record in heartbeat_records:
                message = str(record.record["message"])
                assert heartbeat_substring in message, (
                    f"heartbeat MUST contain '{heartbeat_substring}'"
                    f" (R6 chosen UX); got {message!r}"
                )
                assert "subagent" in message, f"heartbeat MUST contain 'subagent'; got {message!r}"
                assert elapsed_seconds_pattern.search(message) is not None, (
                    f"heartbeat MUST carry an elapsed-seconds field matching \\d+s; got {message!r}"
                )
                assert "ceiling" in message, (
                    f"heartbeat MUST carry the literal 'ceiling' label; got {message!r}"
                )
                ceiling_tail = message.split("ceiling", 1)[1]
                assert ceiling_seconds_pattern.search(ceiling_tail) is not None, (
                    f"heartbeat MUST carry a hard-ceiling-seconds field"
                    f" matching \\d+s after 'ceiling'; got"
                    f" ceiling_tail={ceiling_tail!r}"
                )

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
            assert failure.category == FailureCategory.AGENT, (
                f"OpenCodeResumableExitError with session_id={session_id!r}"
                f" MUST classify as AGENT, got {failure.category!r}"
            )
            assert failure.category != FailureCategory.AMBIGUOUS
            assert failure.resumable_session_id == session_id
            assert failure.counts_against_budget is True
            assert failure.reset_session is False
            assert exc.last_observed_tool_call == "read_file"
            assert exc.last_evidence_summary == ("workspace_change: kind=source weight=1.0")
            assert exc.elapsed_seconds == 420.0
            assert exc.transcript_tail == ("line-1", "line-2")

    def test_r8(self) -> None:
        test_root = Path(__file__).resolve().parent.parent.parent.parent
        for relative_path in RALPH_PIN_TEST_PATHS:
            absolute_path = test_root / relative_path
            assert absolute_path.is_file(), f"Pin test file MUST exist at {absolute_path}"

        assert hasattr(ProcessMonitor, "spawned_subagent_count")
        assert hasattr(ProcessMonitor, "live_subagent_count")

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
