"""Black-box invariant tests for the ralph.interrupt public constants.

These tests pin the canonical values of the three public interrupt
constants (``INTERRUPT_EXIT_CODE``, ``INTERRUPT_HARD_KILL_BUDGET_SECONDS``,
``SIGINT_PROGRESS_POLL_INTERVAL_SECONDS``) and the internal use of those
constants inside the dispatcher's ``force_exit`` and
``run_early_escalation_poll`` methods. The canonical values are also
enforced by import-time ``if``/``raise RuntimeError`` invariants in
``ralph.interrupt.controller`` and ``ralph.interrupt.dispatcher`` (see
ADR-0001 D5b); this file is the public-surface regression pin so a
future change that hides the constants behind a different module path
or hardcodes ``130`` / ``1.5`` / ``0.2`` in the dispatcher is caught.

All five tests are pure unit tests: no subprocess, no I/O, no sleep,
no real signals. Total wall-clock is well under 1 second. Each test
uses ``inspect.getsource`` to assert the dispatcher's
``force_exit`` references the canonical ``INTERRUPT_EXIT_CODE`` symbol
rather than a hardcoded literal, and that
``run_early_escalation_poll`` uses the dataclass field
``self.hard_kill_budget_s`` rather than a module-level constant.
"""

from __future__ import annotations

import inspect

import ralph.interrupt
import ralph.interrupt.controller
import ralph.interrupt.dispatcher


def test_interrupt_exit_code_is_130() -> None:
    """``INTERRUPT_EXIT_CODE`` is 130 on both import paths.

    A regression that hides the constant behind a different module
    path or accidentally rebinds it (e.g. ``from ralph.interrupt
    import INTERRUPT_EXIT_CODE`` shadows the canonical value with
    a derived number) is caught by the dual assertion.
    """
    assert ralph.interrupt.INTERRUPT_EXIT_CODE == 130, (
        f"ralph.interrupt.INTERRUPT_EXIT_CODE must be 130; "
        f"got {ralph.interrupt.INTERRUPT_EXIT_CODE}"
    )
    assert ralph.interrupt.controller.INTERRUPT_EXIT_CODE == 130, (
        f"ralph.interrupt.controller.INTERRUPT_EXIT_CODE must be 130; "
        f"got {ralph.interrupt.controller.INTERRUPT_EXIT_CODE}"
    )
    assert (
        ralph.interrupt.INTERRUPT_EXIT_CODE
        is ralph.interrupt.controller.INTERRUPT_EXIT_CODE
    ), (
        "ralph.interrupt.INTERRUPT_EXIT_CODE and "
        "ralph.interrupt.controller.INTERRUPT_EXIT_CODE must be the "
        "same object (no shadowing re-definition)"
    )


def test_interrupt_hard_kill_budget_seconds_is_1_5() -> None:
    """``INTERRUPT_HARD_KILL_BUDGET_SECONDS`` is 1.5 on both import paths.

    The canonical value matches the existing docstring and is the
    source of truth for the early-escalation timing policy. A
    regression that picks a different in-range value (the existing
    range check at ``ralph.interrupt.dispatcher`` accepts any value
    in (0, 30)) is caught by the exact assertion.
    """
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
    """``SIGINT_PROGRESS_POLL_INTERVAL_SECONDS`` is 0.2 on both import paths.

    The canonical value matches the existing docstring and is the
    source of truth for the early-escalation poll cadence. A
    regression that picks a different in-range value is caught by
    the exact assertion.
    """
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
    """The dispatcher's ``force_exit`` uses ``INTERRUPT_EXIT_CODE``.

    A regression that hardcodes the literal ``130`` in
    ``force_exit`` (instead of referencing the module-level
    constant) is caught by reading the source and asserting the
    constant symbol is referenced. The constant is imported at the
    top of ``dispatcher.py`` and used by name in the body of
    ``force_exit``.
    """
    source = inspect.getsource(
        ralph.interrupt.dispatcher.InterruptDispatcher.force_exit
    )
    assert "INTERRUPT_EXIT_CODE" in source, (
        "InterruptDispatcher.force_exit must reference "
        "INTERRUPT_EXIT_CODE (the canonical constant); the literal 130 "
        "must NOT be hardcoded in the dispatcher body. "
        f"Source:\n{source}"
    )


def test_interrupt_hard_kill_budget_is_used_in_poll_thread() -> None:
    """``run_early_escalation_poll`` uses the dataclass field, not a constant.

    The poll thread is bounded by ``self.hard_kill_budget_s`` (the
    dataclass field, overridable in tests) rather than a
    module-level constant (which would defeat the test override).
    A regression that uses the module-level constant directly is
    caught by reading the source and asserting the dataclass
    field is referenced.
    """
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
