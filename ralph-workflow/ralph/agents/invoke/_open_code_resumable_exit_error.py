"""OpenCodeResumableExitError — deterministic rc=0 classification.

R7 (Trustworthy Idle Watchdog spec) requires that ambiguous agent
exits are diagnosed, classified deterministically, and handled. The
ambiguous case this module owns:

    The agent subprocess exits cleanly (rc=0) BUT the session did
    not produce a completion artifact AND did not call
    ``declare_complete``. The legacy
    ``FailureClassifier.classify`` path would surface this as an
    ambiguous warning (the log line
    ``[flagged_for_review=true]`` was observed in the wild before
    the deterministic classification was added).

Deterministic classification contract (single source of truth):

    1. ``OpenCodeResumableExitError`` is a typed-cause exception that
       the failure classifier MUST recognize BEFORE the broader
       ``AgentInvocationError`` branch. The classifier's typed-cause
       branch lives in ``ralph/recovery/failure_classifier.py`` under
       ``FailureClassifier._categorize_exc`` (the
       ``OpenCodeResumableExitError`` branch precedes the
       ``AgentInvocationError`` branch). This ordering is what makes
       the classification deterministic: the exception NEVER falls
       through to ``FailureCategory.AMBIGUOUS``.

    2. The classifier maps ``OpenCodeResumableExitError`` to
       ``FailureCategory.AGENT`` (NOT ``AMBIGUOUS``) and lifts the
       typed ``resumable_session_id`` attribute so the recovery
       controller can resume the existing session. The classifier's
       typed-cause branch is the only path that consults
       ``getattr(exc, "resumable_session_id", None)``; the broader
       ``AgentInvocationError`` branch does not see this attribute.

    3. ``recovery_action_for_failure_reason("OpenCodeResumableExitError", ...)``
       in ``ralph/agents/invoke/_session_resume.py`` returns:

         * ``"resume"`` when ``has_prior_session=True`` -- the
           existing session carries forward via
           ``resumable_session_id``.
         * ``"fresh"`` when ``has_prior_session=False`` -- no prior
           session to resume; the runner constructs a new session
           via ``fresh_session_options(opts)``.

       The resume and fresh paths are FUNCTION-SEPARATE -- they
       never share code. Watchdog-driven kills NEVER start a new
       session as if beginning new work; only deliberate phase
       transitions may.

    4. NO log line containing ``flagged_for_review=true`` is emitted
       on this exception path (the previous pass added the
       deterministic classification precisely to remove the
       repeated ambiguous-warning spam).

Lock-in regression test:
    ``tests/recovery/test_opencode_resumable_exit_classification.py``
    proves the four contract points above.

References:
    - ``ralph/agents/invoke/_session_resume.py`` for the
      recovery-action contract.
    - ``ralph/recovery/failure_classifier.py:_categorize_exc`` for
      the typed-cause branch ordering.
    - ``ralph/agents/invoke/_errors.py`` for the ``AgentInvocationError``
      base class hierarchy.
"""

from __future__ import annotations

from ralph.agents.invoke._errors import AgentInvocationError


class OpenCodeResumableExitError(AgentInvocationError):
    """Raised when an agent session exits without required completion evidence.

    The session can be continued; the runner maps this into a session-preserving retry.

    The exception carries the captured transport-level session id via
    :attr:`resumable_session_id` so the failure classifier's typed-cause
    branch can thread it through the ``FailureCategory.AGENT`` resume
    path (see module docstring for the full contract).
    """

    def __init__(self, agent_name: str, session_id: str | None = None) -> None:
        self.resumable_session_id = session_id
        super().__init__(
            agent_name,
            0,
            (
                "agent session exited without required completion evidence "
                "(no artifact, no declare_complete)"
            ),
        )


__all__ = ["OpenCodeResumableExitError"]
