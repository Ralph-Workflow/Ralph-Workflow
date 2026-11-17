from __future__ import annotations

import threading
from typing import TYPE_CHECKING, TypeVar

from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._post_exit_verdict import PostExitVerdict
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock
    from ralph.agents.idle_watchdog import TimeoutPolicy

__all__ = [
    "PostExitVerdict",
    "PostExitWatchdog",
    "WatchLoopBase",
]


T = TypeVar("T")


class WatchLoopBase:
    __slots__ = ("_event", "clock")

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._event = threading.Event()

    def wait_until(
        self,
        *,
        predicate: Callable[[], T | None],
        timeout_s: float,
        poll_interval_s: float,
        on_tick: Callable[[T | None], None] | None = None,
    ) -> T | None:
        deadline = self.clock.monotonic() + timeout_s

        result = predicate()
        if result:
            return result

        while True:
            now = self.clock.monotonic()
            if now >= deadline:
                break
            if on_tick is not None:
                on_tick(result)
            remaining = deadline - now
            wait_for = poll_interval_s if remaining >= poll_interval_s else remaining
            self.clock.wait_for_event(self._event, wait_for)
            result = predicate()
            if result:
                return result

        return None

    def signal_activity(self) -> None:
        self._event.set()
        self._event.clear()


class PostExitWatchdog(WatchLoopBase):
    __slots__ = ("_log", "_policy", "last_verdict_reason")

    def __init__(self, policy: TimeoutPolicy, clock: Clock) -> None:
        super().__init__(clock)
        self._policy = policy
        self.last_verdict_reason: PostExitVerdict | None = None
        self._log = logger.bind(component="post_exit_watchdog")

    def wait_for_process_exit(self, predicate_exit_observed: Callable[[], bool]) -> PostExitVerdict:
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
        state = classify_exit_state()
        if state == AgentExecutionState.TERMINAL_COMPLETE:
            return PostExitVerdict.SIGNALS_PRESENT
        if state == AgentExecutionState.RESUMABLE_CONTINUE:
            return PostExitVerdict.QUIESCED_NO_SIGNALS
        return None
