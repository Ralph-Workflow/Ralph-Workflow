"""Post-exit watchdog for agent subprocess and parent-exit timeout enforcement.

PostExitWatchdog owns all wall-clock timeout decisions that occur after the
agent's stdout stream has closed (EOF): the post-EOF process-exit hang window,
the parent-exit grace window, and the descendant-quiesce window.

All wall-clock decisions go through the injected Clock so the watchdog is fully
testable without real sleeps (FakeClock) per CLAUDE.md test performance policy.

This module is the counterpart to ralph.agents.idle_watchdog.IdleWatchdog,
which owns in-stream idle/deadline timeouts.  Every wall-clock timeout fire
path in the agent invocation system routes through one of these two watchdogs;
no ad-hoc clock.monotonic()/clock.sleep() loops are allowed in invoke.py.

Fire-reason precedence:
  1. PROCESS_EXIT_HANG  — PostExitWatchdog.wait_for_process_exit()
  2. DESCENDANT_HANG    — PostExitWatchdog.wait_descendant_quiesce()
  3. (parent-exit grace does not fire; it returns verdicts that map to
     TERMINAL_COMPLETE / WAITING_ON_CHILD / RESUMABLE_CONTINUE)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._post_exit_verdict import PostExitVerdict
from ralph.agents.idle_watchdog._watch_loop_base import WatchLoopBase
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock
    from ralph.agents.idle_watchdog import TimeoutPolicy

__all__ = [
    "PostExitVerdict",
    "PostExitWatchdog",
]


class PostExitWatchdog(WatchLoopBase):
    """Post-exit wall-clock watchdog for process-exit, parent-exit grace, and descendant-wait.

    All three wait methods delegate polling to ``WatchLoopBase.wait_until``
    and use the base's injected Clock so tests can use FakeClock for
    deterministic, sub-second test runs.

    Attributes:
        last_verdict_reason: Set by wait_for_process_exit() when returning
            FIRE_PROCESS_EXIT_HANG; None otherwise.  Exposed so integration
            tests can assert the correct reason without introspecting private state.
    """

    def __init__(self, policy: TimeoutPolicy, clock: Clock) -> None:
        super().__init__(clock)
        self._policy = policy
        self.last_verdict_reason: PostExitVerdict | None = None
        self._log = logger.bind(component="post_exit_watchdog")

    def wait_for_process_exit(self, predicate_exit_observed: Callable[[], bool]) -> PostExitVerdict:
        """Wait for the subprocess to exit within process_exit_wait_seconds.

        Checks the predicate BEFORE the first sleep so an already-exited process
        returns immediately without consuming any clock budget.

        Args:
            predicate_exit_observed: Returns True when the subprocess has exited.

        Returns:
            CONTINUE: subprocess exited (predicate returned True) before deadline.
            FIRE_PROCESS_EXIT_HANG: deadline elapsed with predicate still False.
        """
        result = self.wait_until(
            predicate=lambda: PostExitVerdict.CONTINUE if predicate_exit_observed() else None,
            timeout_s=self._policy.process_exit_wait_seconds,
            poll_interval_s=self._policy.descendant_wait_poll_seconds,
        )

        if result is not None:
            self.last_verdict_reason = PostExitVerdict.CONTINUE
            return PostExitVerdict.CONTINUE

        self.last_verdict_reason = PostExitVerdict.FIRE_PROCESS_EXIT_HANG
        self._log.warning(
            "post-exit watchdog: FIRE reason={} elapsed={}s",
            WatchdogFireReason.PROCESS_EXIT_HANG,
            self._policy.process_exit_wait_seconds,
        )
        return PostExitVerdict.FIRE_PROCESS_EXIT_HANG

    def wait_parent_exit_grace(
        self, classify_exit_state: Callable[[], AgentExecutionState]
    ) -> PostExitVerdict:
        """Wait up to parent_exit_grace_seconds for completion signals or children.

        Polls classify_exit_state() at descendant_wait_poll_seconds intervals.
        The grace window covers the race where MCP-driven background subagents
        have been launched but not yet registered with the ProcessManager.

        Args:
            classify_exit_state: Returns the current AgentExecutionState by
                consulting evaluate_completion + execution_strategy.classify_exit.

        Returns:
            SIGNALS_PRESENT: classify_exit_state returned TERMINAL_COMPLETE.
            CHILDREN_ACTIVE: classify_exit_state returned WAITING_ON_CHILD.
            QUIESCED_NO_SIGNALS: deadline elapsed with RESUMABLE_CONTINUE.
        """
        result = self.wait_until(
            predicate=lambda: self._classify_parent_exit_grace(classify_exit_state),
            timeout_s=self._policy.parent_exit_grace_seconds,
            poll_interval_s=self._policy.descendant_wait_poll_seconds,
        )

        if result is not None:
            self.last_verdict_reason = result
            return result

        state = classify_exit_state()
        if state == AgentExecutionState.TERMINAL_COMPLETE:
            self.last_verdict_reason = PostExitVerdict.SIGNALS_PRESENT
            return PostExitVerdict.SIGNALS_PRESENT
        if state == AgentExecutionState.WAITING_ON_CHILD:
            self.last_verdict_reason = PostExitVerdict.CHILDREN_ACTIVE
            return PostExitVerdict.CHILDREN_ACTIVE
        self.last_verdict_reason = PostExitVerdict.QUIESCED_NO_SIGNALS
        return PostExitVerdict.QUIESCED_NO_SIGNALS

    def wait_descendant_quiesce(
        self, classify_exit_state: Callable[[], AgentExecutionState]
    ) -> PostExitVerdict:
        """Wait for descendant processes to quiesce within descendant_wait_timeout_seconds.

        Polls classify_exit_state() at descendant_wait_poll_seconds intervals.
        If WAITING_ON_CHILD persists for the full deadline, FIRE_DESCENDANT_HANG
        is returned so the caller can fall back to RESUMABLE_CONTINUE semantics
        (raise OpenCodeResumableExitError) rather than treating this as silent success.

        Args:
            classify_exit_state: Returns the current AgentExecutionState.

        Returns:
            SIGNALS_PRESENT: TERMINAL_COMPLETE seen during polling.
            QUIESCED_NO_SIGNALS: RESUMABLE_CONTINUE seen (tree quiet before deadline).
            FIRE_DESCENDANT_HANG: deadline elapsed with WAITING_ON_CHILD persistent.
        """
        result = self.wait_until(
            predicate=lambda: self._classify_descendant_quiesce(classify_exit_state),
            timeout_s=self._policy.descendant_wait_timeout_seconds,
            poll_interval_s=self._policy.descendant_wait_poll_seconds,
        )

        if result is not None:
            self.last_verdict_reason = result
            return result

        state = classify_exit_state()
        if state == AgentExecutionState.TERMINAL_COMPLETE:
            self.last_verdict_reason = PostExitVerdict.SIGNALS_PRESENT
            return PostExitVerdict.SIGNALS_PRESENT
        if state == AgentExecutionState.WAITING_ON_CHILD:
            self.last_verdict_reason = PostExitVerdict.FIRE_DESCENDANT_HANG
            self._log.warning(
                "post-exit watchdog: FIRE reason={} elapsed={}s",
                WatchdogFireReason.DESCENDANT_HANG,
                self._policy.descendant_wait_timeout_seconds,
            )
            return PostExitVerdict.FIRE_DESCENDANT_HANG
        self.last_verdict_reason = PostExitVerdict.QUIESCED_NO_SIGNALS
        return PostExitVerdict.QUIESCED_NO_SIGNALS

    @staticmethod
    def _classify_parent_exit_grace(
        classify_exit_state: Callable[[], AgentExecutionState],
    ) -> PostExitVerdict | None:
        """Classify exit state for parent-exit grace; return verdict or None to keep polling."""
        state = classify_exit_state()
        if state == AgentExecutionState.TERMINAL_COMPLETE:
            return PostExitVerdict.SIGNALS_PRESENT
        if state == AgentExecutionState.WAITING_ON_CHILD:
            return PostExitVerdict.CHILDREN_ACTIVE
        return None

    @staticmethod
    def _classify_descendant_quiesce(
        classify_exit_state: Callable[[], AgentExecutionState],
    ) -> PostExitVerdict | None:
        """Classify exit state for descendant quiesce; return verdict or None to keep polling."""
        state = classify_exit_state()
        if state == AgentExecutionState.TERMINAL_COMPLETE:
            return PostExitVerdict.SIGNALS_PRESENT
        if state == AgentExecutionState.RESUMABLE_CONTINUE:
            return PostExitVerdict.QUIESCED_NO_SIGNALS
        return None
