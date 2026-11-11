from __future__ import annotations

import inspect

import ralph.interrupt
import ralph.interrupt.controller
import ralph.interrupt.dispatcher


def test_interrupt_exit_code_is_130() -> None:
    assert ralph.interrupt.INTERRUPT_EXIT_CODE == 130, (
        f"ralph.interrupt.INTERRUPT_EXIT_CODE must be 130; "
        f"got {ralph.interrupt.INTERRUPT_EXIT_CODE}"
    )
    assert ralph.interrupt.controller.INTERRUPT_EXIT_CODE == 130, (
        f"ralph.interrupt.controller.INTERRUPT_EXIT_CODE must be 130; "
        f"got {ralph.interrupt.controller.INTERRUPT_EXIT_CODE}"
    )
    assert ralph.interrupt.INTERRUPT_EXIT_CODE is ralph.interrupt.controller.INTERRUPT_EXIT_CODE, (
        "ralph.interrupt.INTERRUPT_EXIT_CODE and "
        "ralph.interrupt.controller.INTERRUPT_EXIT_CODE must be the "
        "same object (no shadowing re-definition)"
    )


def test_interrupt_hard_kill_budget_seconds_is_1_5() -> None:
    assert ralph.interrupt.INTERRUPT_HARD_KILL_BUDGET_SECONDS == 1.5, (
        f"ralph.interrupt.INTERRUPT_HARD_KILL_BUDGET_SECONDS must be 1.5; "
        f"got {ralph.interrupt.INTERRUPT_HARD_KILL_BUDGET_SECONDS}"
    )
    assert ralph.interrupt.dispatcher.INTERRUPT_HARD_KILL_BUDGET_SECONDS == 1.5, (
        f"ralph.interrupt.dispatcher.INTERRUPT_HARD_KILL_BUDGET_SECONDS "
        f"must be 1.5; got {ralph.interrupt.dispatcher.INTERRUPT_HARD_KILL_BUDGET_SECONDS}"
    )
    assert (
        ralph.interrupt.INTERRUPT_HARD_KILL_BUDGET_SECONDS
        is ralph.interrupt.dispatcher.INTERRUPT_HARD_KILL_BUDGET_SECONDS
    ), (
        "ralph.interrupt.INTERRUPT_HARD_KILL_BUDGET_SECONDS and "
        "ralph.interrupt.dispatcher.INTERRUPT_HARD_KILL_BUDGET_SECONDS "
        "must be the same object (no shadowing re-definition)"
    )


def test_sigint_progress_poll_interval_seconds_is_0_2() -> None:
    assert ralph.interrupt.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS == 0.2, (
        f"ralph.interrupt.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS must be 0.2; "
        f"got {ralph.interrupt.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS}"
    )
    assert ralph.interrupt.dispatcher.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS == 0.2, (
        f"ralph.interrupt.dispatcher.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS "
        f"must be 0.2; got {ralph.interrupt.dispatcher.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS}"
    )
    assert (
        ralph.interrupt.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS
        is ralph.interrupt.dispatcher.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS
    ), (
        "ralph.interrupt.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS and "
        "ralph.interrupt.dispatcher.SIGINT_PROGRESS_POLL_INTERVAL_SECONDS "
        "must be the same object (no shadowing re-definition)"
    )


def test_interrupt_exit_code_in_dispatcher_hard_exit_path() -> None:
    source = inspect.getsource(ralph.interrupt.dispatcher.InterruptDispatcher.force_exit)
    assert "INTERRUPT_EXIT_CODE" in source, (
        "InterruptDispatcher.force_exit must reference "
        "INTERRUPT_EXIT_CODE (the canonical constant); the literal 130 "
        "must NOT be hardcoded in the dispatcher body. "
        f"Source:\n{source}"
    )


def test_interrupt_hard_kill_budget_is_used_in_poll_thread() -> None:
    source = inspect.getsource(
        ralph.interrupt.dispatcher.InterruptDispatcher.run_early_escalation_poll
    )
    assert "self.hard_kill_budget_s" in source, (
        "InterruptDispatcher.run_early_escalation_poll must bound the "
        "deadline by self.hard_kill_budget_s (the dataclass field), not "
        "a module-level constant; otherwise the test override is "
        "defeated. "
        f"Source:\n{source}"
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-q"])
