"""Regression test: ``SESSION_CEILING_EXCEEDED`` MUST NOT resume the prior session.

The PROMPT log showed that ``SESSION_CEILING_EXCEEDED`` (the
operator-set absolute max-session wall-clock) was being routed
through the recovery controller's resume path in some flows, causing
the next attempt to silently inherit a session that had already
exceeded the operator's hard cap. The canonical contract pinned at
``_process_reader._RESUMABLE_FIRE_REASONS`` is:

  * ``SESSION_CEILING_EXCEEDED`` MUST restart from a fresh
    session -- the operator's hard cap is an absolute bound, not
    a transient stall signal.

This test pins the contract by exercising the public
``recovery_action_for_failure_reason(...)`` helper directly:

  * With ``has_prior_session=True``, ``SESSION_CEILING_EXCEEDED``
    MUST return ``'fresh'`` (not ``'resume'``).
  * With ``has_prior_session=False``, it MUST also return
    ``'fresh'``.

The helper is the single source of truth consulted by the recovery
controller's resume path so any regression here surfaces as a fresh
test failure with a clear assertion message.
"""

from __future__ import annotations

from ralph.agents.invoke._session_resume import recovery_action_for_failure_reason


def test_session_ceiling_exceeded_with_prior_session_returns_fresh() -> None:
    """``SESSION_CEILING_EXCEEDED`` with a prior session MUST return ``'fresh'``.

    The session ceiling is the operator's ABSOLUTE max-session
    wall-clock cap; resuming the prior session would re-enter the
    same cap window and re-fire the same reason in a loop. The
    recovery controller MUST restart from a fresh session so the
    next attempt has a fresh wall-clock budget.
    """
    action = recovery_action_for_failure_reason("SESSION_CEILING_EXCEEDED", has_prior_session=True)
    assert action == "fresh", (
        f"SESSION_CEILING_EXCEEDED with has_prior_session=True MUST"
        f" return 'fresh' (operator-set hard cap cannot be"
        f" resumed); got {action!r}"
    )


def test_session_ceiling_exceeded_without_prior_session_returns_fresh() -> None:
    """``SESSION_CEILING_EXCEEDED`` without a prior session MUST return ``'fresh'``."""
    action = recovery_action_for_failure_reason("SESSION_CEILING_EXCEEDED", has_prior_session=False)
    assert action == "fresh"


def test_session_ceiling_exceeded_does_not_resume() -> None:
    """``SESSION_CEILING_EXCEEDED`` MUST NOT return ``'resume'`` or ``'new_session_with_id'``."""
    for prior in (True, False):
        action = recovery_action_for_failure_reason(
            "SESSION_CEILING_EXCEEDED", has_prior_session=prior
        )
        assert action != "resume", (
            f"SESSION_CEILING_EXCEEDED with has_prior_session={prior}"
            f" MUST NOT return 'resume'; got {action!r}"
        )
        assert action != "new_session_with_id", (
            f"SESSION_CEILING_EXCEEDED with has_prior_session={prior}"
            f" MUST NOT return 'new_session_with_id'; got {action!r}"
        )


def test_session_ceiling_is_in_non_resumable_set() -> None:
    """Pin the inverse: ``SESSION_CEILING_EXCEEDED`` is in the non-resumable set.

    The canonical contract pinned at
    ``tests/agents/idle_watchdog/test_resume_after_kill_contract.py``
    treats ``SESSION_CEILING_EXCEEDED`` as a NON-RESUMABLE fire
    reason. This test asserts the public helper honours the
    contract end-to-end.
    """
    # SESSION_CEILING_EXCEEDED is the canonical example of an
    # operator-set hard cap. Compare against CHILDREN_PERSIST_TOO_LONG
    # (also non-resumable, also stuck-job detector).
    hard_caps = ("SESSION_CEILING_EXCEEDED", "CHILDREN_PERSIST_TOO_LONG")
    for cap in hard_caps:
        action = recovery_action_for_failure_reason(cap, has_prior_session=True)
        assert action == "fresh", f"hard-cap reason={cap!r} MUST NOT resume; got {action!r}"
