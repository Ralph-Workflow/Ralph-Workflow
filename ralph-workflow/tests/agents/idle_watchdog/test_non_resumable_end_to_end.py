"""End-to-end boundary tests for non-resumable fire reasons.

The four non-resumable fire reasons (``PROCESS_EXIT_HANG``,
``DESCENDANT_HANG``, ``SESSION_CEILING_EXCEEDED``,
``CHILDREN_PERSIST_TOO_LONG``) must never resume the prior session. We
drive the full chain: watchdog fire -> ``_IdleStreamTimeoutError`` ->
``AgentInactivityTimeoutError`` (``session_resume_safe=False``) -> recovery
session resolution returns ``None`` -> recovery action is ``fresh`` -> the
resulting ``AgentRetryIntent`` requests a fresh session.

For the two post-exit reasons we exercise ``PostExitWatchdog``; for the two
in-stream reasons we exercise ``_ProcessLineReader._check_fire``.

All tests use FakeClock and injected fakes; no real subprocess, no
``time.sleep``, no real network.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    PostExitVerdict,
    PostExitWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.invoke._agent_inactivity_timeout_error import (
    AgentInactivityTimeoutError,
)
from ralph.agents.invoke._errors import _IdleStreamTimeoutError
from ralph.agents.invoke._process_reader import (
    _convert_idle_stream_timeout_to_agent_error,
    _ProcessLineReader,
)
from ralph.agents.invoke._session_resume import (
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_retry_intent import agent_retry_intent_for_failure
from ralph.pipeline.effect_executor import _failure_requires_fresh_session

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent


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


class _FakeManagedProcess:
    """Fake process handle for ``_ProcessLineReader._check_fire``."""

    def __init__(self) -> None:
        self.pid: int | None = None
        self.terminate_calls: list[float] = []

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        self.terminate_calls.append(grace_period_s)


@dataclass
class _FakeCheckFireSelf:
    """Minimal fake reader self for calling ``_ProcessLineReader._check_fire``."""

    _policy: TimeoutPolicy
    _clock: FakeClock
    _lines_lock: threading.Lock = field(default_factory=threading.Lock)
    _lines_queue: list[str] = field(default_factory=list)
    _last_hard_stop: list[WaitingStatusEvent | None] = field(
        default_factory=lambda: [None]
    )
    _last_activity_kind: str = "none"
    _handle: _FakeManagedProcess = field(default_factory=_FakeManagedProcess)


def _waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_watchdog_for_waiting_fire() -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog configured to fire CHILDREN_PERSIST_TOO_LONG quickly."""
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=5.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        max_waiting_on_child_seconds=15.0,
        suspect_waiting_on_child_seconds=5.0,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        os_descendant_only_suspect_seconds=None,
        waiting_status_interval_seconds=100.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor()), clock


def _make_watchdog_for_session_ceiling() -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog configured to fire SESSION_CEILING_EXCEEDED quickly."""
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=5.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        max_session_seconds=10.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor()), clock


def _fire_in_stream_reason(
    reason: WatchdogFireReason,
) -> tuple[list[str], _IdleStreamTimeoutError]:
    """Drive ``IdleWatchdog.evaluate`` and ``_ProcessLineReader._check_fire``.

    Returns the pending lines and the wrapper exception carrying ``reason``.
    """
    if reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
        watchdog, clock = _make_watchdog_for_session_ceiling()
        watchdog.record_invocation_start()
        clock.advance(11.0)
        verdict = watchdog.evaluate(classify_quiet=_active)
    else:
        watchdog, clock = _make_watchdog_for_waiting_fire()
        watchdog.record_invocation_start()
        # First evaluation enters WAITING_ON_CHILD after the idle deadline.
        clock.advance(6.0)
        verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
        assert verdict == WatchdogVerdict.WAITING_ON_CHILD
        # Second evaluation fires once the cumulative ceiling is reached.
        clock.advance(15.0)
        verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)

    assert verdict == WatchdogVerdict.FIRE, f"expected FIRE for {reason}; got {verdict}"
    assert watchdog.last_fire_reason == reason

    fake_self = _FakeCheckFireSelf(_policy=watchdog._config, _clock=clock)
    result = _ProcessLineReader._check_fire(fake_self, watchdog, WatchdogVerdict.FIRE)
    assert result is not None
    pending_lines, wrapper = result
    assert isinstance(wrapper, _IdleStreamTimeoutError)
    assert wrapper.reason == reason
    return pending_lines, wrapper


def _fire_process_exit_hang() -> _IdleStreamTimeoutError:
    """Drive ``PostExitWatchdog.wait_for_process_exit`` to fire."""
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        process_exit_wait_seconds=5.0,
        descendant_wait_poll_seconds=0.1,
    )
    post_exit = PostExitWatchdog(policy, clock)
    verdict = post_exit.wait_for_process_exit(lambda: False)
    assert verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG
    return _IdleStreamTimeoutError(
        policy.process_exit_wait_seconds,
        WatchdogFireReason.PROCESS_EXIT_HANG,
    )


def _fire_descendant_hang() -> _IdleStreamTimeoutError:
    """Drive ``PostExitWatchdog.wait_descendant_quiesce`` to fire."""
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        descendant_wait_timeout_seconds=5.0,
        descendant_wait_poll_seconds=0.1,
    )
    post_exit = PostExitWatchdog(policy, clock)
    verdict = post_exit.wait_descendant_quiesce(
        lambda: AgentExecutionState.WAITING_ON_CHILD
    )
    assert verdict == PostExitVerdict.FIRE_DESCENDANT_HANG
    return _IdleStreamTimeoutError(
        policy.descendant_wait_timeout_seconds,
        WatchdogFireReason.DESCENDANT_HANG,
    )


def _convert_reason_to_agent_error(
    wrapper: _IdleStreamTimeoutError,
    pending_lines: tuple[str, ...] | list[str] = (),
) -> AgentInactivityTimeoutError:
    """Convert the wrapper through the canonical invocation-layer seam."""
    return _convert_idle_stream_timeout_to_agent_error(
        agent_name="test-agent",
        exc=wrapper,
        parsed_output=pending_lines,
        explicit_completion_seen=False,
        captured_session_id=None,
        expected_session_id="prior-session-abc",
    )


def _resolve_recovery_session_id_for_test(exc: AgentInactivityTimeoutError) -> str | None:
    """Resolve the session id the same way the pipeline executor does."""
    if _failure_requires_fresh_session(exc, AgentInactivityTimeoutError):
        return None
    return getattr(exc, "resumable_session_id", None) or None


def _assert_non_resumable_recovery_chain(exc: AgentInactivityTimeoutError) -> None:
    """Assert the full recovery chain refuses to resume for ``exc``."""
    assert exc.session_resume_safe is False, (
        f"reason={exc.reason!r}: session_resume_safe must be False;"
        f" got {exc.session_resume_safe}"
    )
    session_id = _resolve_recovery_session_id_for_test(exc)
    assert session_id is None, (
        f"non-resumable reason={exc.reason!r} must clear the resolved session id;"
        f" got {session_id!r}"
    )
    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=bool(session_id),
    )
    assert action == "fresh", (
        f"non-resumable reason={exc.reason!r} must map to fresh; got {action!r}"
    )
    resolved = resolve_resume_session_id(
        has_prior_session=False,
        prior_session_id="prior-session-abc",
        recovery_action=action,
    )
    assert resolved is None
    intent = agent_retry_intent_for_failure(
        failure_reason="AgentInactivityTimeoutError",
        session_id=resolved,
        reset_tool_registry=False,
    )
    assert intent.action == "fresh", (
        f"non-resumable reason={exc.reason!r} must yield fresh intent;"
        f" got {intent.action!r}"
    )
    assert intent.session_id is None, (
        f"non-resumable reason={exc.reason!r} must yield intent.session_id=None;"
        f" got {intent.session_id!r}"
    )


def test_process_exit_hang_does_not_resume() -> None:
    """PROCESS_EXIT_HANG refuses to resume the prior session."""
    wrapper = _fire_process_exit_hang()
    timeout_exc = _convert_reason_to_agent_error(wrapper)
    assert timeout_exc.reason == WatchdogFireReason.PROCESS_EXIT_HANG
    _assert_non_resumable_recovery_chain(timeout_exc)


def test_descendant_hang_does_not_resume() -> None:
    """DESCENDANT_HANG refuses to resume the prior session."""
    wrapper = _fire_descendant_hang()
    timeout_exc = _convert_reason_to_agent_error(wrapper)
    assert timeout_exc.reason == WatchdogFireReason.DESCENDANT_HANG
    _assert_non_resumable_recovery_chain(timeout_exc)


def test_session_ceiling_exceeded_does_not_resume() -> None:
    """SESSION_CEILING_EXCEEDED refuses to resume the prior session."""
    pending_lines, wrapper = _fire_in_stream_reason(
        WatchdogFireReason.SESSION_CEILING_EXCEEDED
    )
    timeout_exc = _convert_reason_to_agent_error(wrapper, pending_lines)
    assert timeout_exc.reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    _assert_non_resumable_recovery_chain(timeout_exc)


def test_children_persist_too_long_does_not_resume() -> None:
    """CHILDREN_PERSIST_TOO_LONG refuses to resume the prior session."""
    pending_lines, wrapper = _fire_in_stream_reason(
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    )
    timeout_exc = _convert_reason_to_agent_error(wrapper, pending_lines)
    assert timeout_exc.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    _assert_non_resumable_recovery_chain(timeout_exc)
