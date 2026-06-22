"""Regression test: ``PROCESS_EXIT_HANG`` is NOT a resumable failure reason.

The PROMPT log showed that when the watchdog fired
``PROCESS_EXIT_HANG`` (the post-exit detect for an agent that closed
stdout but did not exit within the configured grace), the recovery
controller's ``recovery_action_for_failure_reason(...)`` returned
``'resume'`` in some cases, causing the next attempt to silently
inherit the half-dead prior session state. The canonical contract
pinned at ``_process_reader._RESUMABLE_FIRE_REASONS`` is:

  * ``PROCESS_EXIT_HANG``, ``DESCENDANT_HANG``,
    ``SESSION_CEILING_EXCEEDED``, ``CHILDREN_PERSIST_TOO_LONG``,
    ``DEFERRED_BY_STUCK_CLASSIFIER`` MUST restart from a fresh
    session (the half-dead process tree cannot be safely continued).
  * Only the 6 production fire reasons plus
    ``REPEATED_IDENTICAL_TOOL_CALL`` are resumable.

This test pins the contract by exercising the public
``recovery_action_for_failure_reason(...)`` helper directly:

  * With ``has_prior_session=True`` (the dangerous case), the
    ``PROCESS_EXIT_HANG`` reason MUST return ``'fresh'``.
  * With ``has_prior_session=False``, it MUST also return ``'fresh'``.

The helper is the single source of truth consulted by the recovery
controller's resume path so any regression here surfaces as a fresh
test failure with a clear assertion message.
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke._session_resume import recovery_action_for_failure_reason


@pytest.mark.parametrize(
    "failure_reason",
    (
        "PROCESS_EXIT_HANG",
        "DESCENDANT_HANG",
        "CHILDREN_PERSIST_TOO_LONG",
        "DEFERRED_BY_STUCK_CLASSIFIER",
    ),
)
def test_post_exit_watchdog_fire_reasons_do_not_resume_with_prior_session(
    failure_reason: str,
) -> None:
    """PostExitWatchdog fire reasons MUST NOT resume the prior session.

    A post-exit hang (PROCESS_EXIT_HANG) means the agent process
    closed stdout but did not exit within the grace window. The
    process tree is in an indeterminate half-dead state and cannot
    be safely continued via the prior session id; the next attempt
    MUST restart from a fresh session.
    """
    action = recovery_action_for_failure_reason(
        failure_reason, has_prior_session=True
    )
    assert action == "fresh", (
        f"failure_reason={failure_reason!r} with has_prior_session=True"
        f" MUST return 'fresh' (the half-dead process tree cannot"
        f" be safely resumed); got {action!r}"
    )


def test_process_exit_hang_without_prior_session_returns_fresh() -> None:
    """PROCESS_EXIT_HANG without a prior session MUST also return 'fresh'."""
    action = recovery_action_for_failure_reason(
        "PROCESS_EXIT_HANG", has_prior_session=False
    )
    assert action == "fresh"


def test_process_exit_hang_is_not_in_resumable_set() -> None:
    """PROCESS_EXIT_HANG MUST NOT be in the canonical resumable-fire-reason set.

    Mirrors the contract pinned at
    ``tests/agents/idle_watchdog/test_resume_after_kill_contract.py``
    and enforced by ``_process_reader._RESUMABLE_FIRE_REASONS``.
    """
    action = recovery_action_for_failure_reason(
        "PROCESS_EXIT_HANG", has_prior_session=True
    )
    assert action != "resume", (
        f"PROCESS_EXIT_HANG MUST NOT resume (the post-exit hang is"
        f" a half-dead process tree); got {action!r}"
    )
    assert action != "new_session_with_id", (
        f"PROCESS_EXIT_HANG MUST NOT new_session_with_id (the prior"
        f" session id is unsafe to reuse); got {action!r}"
    )


def test_resumable_reasons_still_resume_for_prior_session() -> None:
    """Sanity check: the canonical resumable exception class names still resume.

    Pins the inverse of the regression contract: the EXISTING
    resumable EXCEPTION CLASS NAMES must continue to return
    ``'resume'`` so the agent-attributed watchdog kills still
    continue the prior session as designed. The helper at
    ``recovery_action_for_failure_reason`` matches on exception
    class name strings (``AgentInactivityTimeoutError``,
    ``OpenCodeResumableExitError``), NOT on watchdog fire-reason
    enum strings (``NO_OUTPUT_AT_START`` etc.) -- those reach the
    helper wrapped in an ``AgentInactivityTimeoutError`` already.
    """
    resumable = (
        "AgentInactivityTimeoutError",
        "OpenCodeResumableExitError",
    )
    for reason in resumable:
        action = recovery_action_for_failure_reason(
            reason, has_prior_session=True
        )
        assert action == "resume", (
            f"failure_reason={reason!r} with has_prior_session=True"
            f" MUST return 'resume' (sanity check of the resumable"
            f" set); got {action!r}"
        )
