"""Single source of truth for the agent session-resume / session-create policy.

Two orthogonal concerns are kept deliberately separate so neither can drift:

- :func:`resolve_resume_session_id` is the ONLY place that decides WHETHER the
  next attempt continues the prior agent session and WHICH session id it
  threads. It maps a recovery action (``fresh``/``resume``/
  ``new_session_with_id``) to the session id the caller records in pipeline
  state (or ``None`` for a fresh session).
- The per-transport resume FLAG SYNTAX (``--resume {}`` for Claude Code,
  ``--session {}`` for OpenCode, ``None`` for transports without resume) lives
  exclusively in each agent's ``session_flag`` template (see
  ``ralph.agents.registry``). Every command builder emits the flag via
  ``config.session_flag``; no builder hardcodes the flag string. This keeps the
  syntax single-sourced and honours custom agent configurations uniformly.

- :func:`recovery_action_for_failure_reason` is the ONLY mapping from a stored
  failure reason to a recovery action.

The pre-fix code had a divergent ``elif`` branch in
``_build_claude_interactive_command`` that emitted ``--session-id``
(create a new session with this id) for the interactive-Claude path, silently
turning the resume path into a fresh session. Routing every builder through
``config.session_flag`` makes that divergence structurally impossible.

## Resumable session id threading end-to-end (single source of truth)

This module is the contract surface for the resume-after-watchdog-kill path.
The seven numbered evidence points below trace the resumable session id from
the watchdog kill to the recovery controller's ``resume`` action. Any future
PR that touches ONE of these points MUST keep the others consistent so a
watchdog kill ALWAYS resumes the existing session and never silently starts
work over:

  1. ``ralph/agents/idle_watchdog_kill.py`` defines
     ``IdleWatchdogKilledError(reason, signal, *, evidence_summary=None,
     child_alive=None, resumable_session_id=None)``. The
     ``resumable_session_id`` kwarg is the canonical carrier of the
     captured session id; without it the watchdog kill cannot be resumed.

  2. Both ``ralph/agents/invoke/_process_reader.py:615`` and
     ``ralph/agents/invoke/_pty_line_reader.py:681`` construct
     ``IdleWatchdogKilledError(..., resumable_session_id=captured_session_id)``
     in the fire path. The captured id is sourced from
     ``self._captured_session_id`` which is populated from the visible-TUI
     extractor or from the agent's ``--session`` flag at invocation start.

  3. ``_convert_idle_stream_timeout_to_agent_error`` (line 144) propagates
     ``resumable_session_id`` to :class:`AgentInactivityTimeoutError` via
     ``InactivityTimeoutOpts(resumable_session_id=captured_session_id or
     expected_session_id, ...)``. The error class exposes
     ``AgentInactivityTimeoutError.resumable_session_id`` so the classifier
     and the controller can both read the captured id end-to-end.

  4. ``ralph/recovery/failure_classifier.py`` lines 751-755 contain the
     ``resumable_kill`` carve-out: when ``watchdog_reason ==
     "no_output_at_start"`` AND ``resumable_session_id`` is present, the
     classifier suppresses ``is_unavailable`` (``base_unavailable and not
     resumable_kill``). This is the bridge that turns a watchdog kill into
     a retryable resume instead of a chain advance.

  5. ``ralph/recovery/controller.py`` lines 690-692 sets
     ``state.last_agent_session_id = failure.resumable_session_id`` when
     ``retry_in_session and failure.resumable_session_id``. The carve-out
     is mutually exclusive with ``failure.reset_session`` (a stale-session
     reset clears ``last_agent_session_id`` instead). The downstream
     ``_apply_chain_retry`` consumer then calls
     ``resume_agent_retry_intent(state.last_agent_session_id)``.

  6. ``recovery_action_for_failure_reason('AgentInactivityTimeoutError',
     has_prior_session=True)`` (this module, lines 109-158) returns
     ``'resume'``. The mapping is narrow and explicit: ONLY
     ``AgentInactivityTimeoutError`` and ``OpenCodeResumableExitError``
     with ``has_prior_session=True`` resume the prior session; every
     other failure reason with a prior session returns ``'fresh'``.

  7. ``fresh_session_options(opts)`` (this module, lines 31-55) returns
     ``InvokeOptions(session_id=None)`` for deliberate phase transitions
     only. The ``prior_session_id`` parameter is accepted for
     forward-compatibility but is NEVER written back into ``session_id``.
     Resume (``resolve_resume_session_id``) and fresh
     (``fresh_session_options``) are FUNCTION-SEPARATE so neither path
     can silently regress into the other.

Lock-in regression test: ``tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py``
plus the existing
``tests/agents/idle_watchdog/test_resume_after_kill_contract.py``,
``test_resume_after_kill_watchdog_boundary.py``, and
``test_resume_contract_invariant.py`` exercise the seven points above.
Any change to the resume path MUST keep all four passing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.invoke._types import InvokeOptions
    from ralph.pipeline.agent_retry_intent import AgentRetryAction


def fresh_session_options(
    opts: InvokeOptions,
    *,
    prior_session_id: str | None = None,
) -> InvokeOptions:
    """Return a NEW ``InvokeOptions`` instance with ``session_id`` cleared.

    Used by every ordinary new-phase transition so the new phase always
    starts a fresh session, even when the prior phase recovered via
    ``resolve_resume_session_id`` and threaded an id forward.

    The ``prior_session_id`` parameter is accepted for forward
    compatibility but MUST NOT be written back into ``session_id`` —
    ordinary new-phase transitions are explicitly fresh.

    Pure function: no side effects, no I/O, no clock reads.
    """
    del prior_session_id  # accepted for API forward-compatibility; never written.
    return replace(opts, session_id=None)


def resolve_resume_session_id(
    *,
    has_prior_session: bool,
    prior_session_id: str | None,
    recovery_action: str,
) -> str | None:
    """Return the session id to thread into the next attempt, or None for fresh.

    This is the single decision point for session continuation. The
    per-transport resume flag SYNTAX is owned separately by each agent's
    ``session_flag`` template; this helper only decides the id.

    Args:
        has_prior_session: True when the orchestrator has a prior session
            id to continue from. When False, the helper always returns None.
        prior_session_id: The session id from the prior attempt. May be
            None when ``has_prior_session`` is False; must be non-empty
            when ``has_prior_session`` is True.
        recovery_action: The decision the recovery controller made. One of:

              - ``"fresh"``: ignore any prior session id; start anew.
              - ``"resume"``: continue the prior session.
              - ``"new_session_with_id"``: reuse the supplied id for a new
                session (transports that accept a creation-time session id).

    Returns:
        The session id the caller should record in pipeline state and thread
        into the agent invocation, or None to start a fresh session.

    Raises:
        ValueError: When ``recovery_action`` is unknown or
            ``has_prior_session=True`` but ``prior_session_id`` is empty/None.
    """
    if recovery_action not in {"fresh", "resume", "new_session_with_id"}:
        raise ValueError(
            f"unknown recovery_action: {recovery_action!r}; "
            "expected one of 'fresh', 'resume', 'new_session_with_id'"
        )
    if has_prior_session and not (isinstance(prior_session_id, str) and prior_session_id):
        raise ValueError("has_prior_session=True requires a non-empty prior_session_id")

    if recovery_action == "fresh" or not has_prior_session:
        return None
    return prior_session_id if isinstance(prior_session_id, str) else None


def recovery_action_for_failure_reason(
    failure_reason: str,
    *,
    has_prior_session: bool,
    reset_tool_registry: bool = False,
) -> AgentRetryAction:
    """Map a stored failure reason to a recovery action.

    Used by the pipeline executor to decide whether the next attempt
    should ``resume`` the prior session, request a ``new_session_with_id``
    (e.g. on a stale-session error), or start ``fresh`` (no prior
    session to resume).

    The mapping is intentionally narrow and explicit:

    - ``AgentInactivityTimeoutError`` (with a prior session) -> ``resume``
    - ``OpenCodeResumableExitError`` (with a prior session) -> ``resume``
    - tool-availability failure (with a prior session AND
      ``reset_tool_registry=True``) -> ``resume`` (NEW BEHAVIOR; the
      pre-fix code returned ``fresh`` here, which made every
      tool-availability retry re-read the prompt).
    - stale/invalid session id family (with a prior session)
      -> ``fresh``
    - everything else -> ``fresh``

    Args:
        failure_reason: The exception class name (or wire-level error
            substring) from the last failed attempt. Empty string
            means "no failure recorded" (the cleared state on the
            success path).
        has_prior_session: True when the orchestrator has a prior
            session id to resume (or annotate a fresh session with).
        reset_tool_registry: NEW BEHAVIOR. When True, indicates the
            last failure was classified as a tool-availability
            failure (the live wire-level ``No such tool available:
            mcp__<server>__<tool>`` error). The helper returns
            ``'resume'`` instead of ``'fresh'`` so the next attempt
            continues the prior session (the tool registry has been
            rebuilt via ``RestartAwareMcpBridge.reset_tool_registry()``
            so a fresh session is unnecessary). Defaults to False to
            preserve the existing behavior on the pre-existing
            branches.
    """
    if not has_prior_session:
        return "fresh"
    if failure_reason in {
        "AgentInactivityTimeoutError",
        "OpenCodeResumableExitError",
    }:
        return "resume"
    if reset_tool_registry:
        return "resume"
    # Everything else — including the stale/invalid session id family — starts
    # fresh: a prior session that did not match a resume-worthy failure reason
    # cannot be safely continued.
    return "fresh"


__all__ = [
    "fresh_session_options",
    "recovery_action_for_failure_reason",
    "resolve_resume_session_id",
]
