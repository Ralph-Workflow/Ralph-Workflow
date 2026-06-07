"""Single source of truth for the agent session-resume / session-create policy.

Public surface:

- :func:`resolve_session_resume_flag` is the ONLY function in the Ralph
  codebase that knows Claude Code's ``--resume`` vs ``--session-id`` flag
  semantics. All call sites that need to decide which flag to emit
  delegate to this helper. The agent configuration's ``session_flag``
  template (e.g. ``"--resume {}"`` or ``"--session {}"``) is the
  per-transport source of truth for the resume syntax.

The pre-fix code had a SINGLE divergent ``elif`` branch in
``_build_claude_interactive_command`` that emitted ``--session-id``
(create a new session with this id) for the interactive-Claude path.
That was a real-session-continuation bug: Claude Code treats
``--session-id`` and ``--resume`` as two different flags with two
different semantics, so the resume path was silently a fresh session.

The fix routes ALL resume-vs-create decisions through
``resolve_session_resume_flag`` so the policy lives in one place and
cannot drift between call sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.config.enums import AgentTransport

# Authoritative mapping for the built-in transports. Custom agent
# configurations with a non-empty ``session_flag`` template override
# these defaults at the agent-config layer.
_DEFAULT_RESUME_FLAGS: dict[str, str] = {
    "claude": "--resume",
    "claude_interactive": "--resume",
    "opencode": "--session",
}


def resolve_session_resume_flag(
    transport: AgentTransport | str,
    *,
    has_prior_session: bool,
    prior_session_id: str | None,
    recovery_action: str,
) -> tuple[list[str], str | None]:
    """Return the (cmd_args, new_session_id) pair for the given transport.

    Args:
        transport: The agent transport (``AgentTransport`` enum value or
            a string with the same name). Determines the resume-flag
            syntax (e.g. ``--resume`` for Claude Code, ``--session``
            for OpenCode).
        has_prior_session: True when the orchestrator has a prior session
            id to resume (or annotate a fresh session with). When False,
            the helper returns no extra args and a None session id.
        prior_session_id: The session id from the prior attempt. May be
            None when ``has_prior_session`` is False; must be non-empty
            when ``has_prior_session`` is True.
        recovery_action: The decision the recovery controller made. One
            of:
              - ``"fresh"``: ignore any prior session id; let the agent
                create a brand-new session.
              - ``"resume"``: continue the prior session.
              - ``"new_session_with_id"``: brand-new session whose id is
                the supplied prior_session_id. (Used by transports
                that accept a creation-time session id.)

    Returns:
        A 2-tuple ``(extra_cmd_args, new_session_id_for_state)``. The
        ``extra_cmd_args`` list contains zero or more CLI flag tokens
        to append to the agent command line. The
        ``new_session_id_for_state`` is the session id the caller
        should record in pipeline state, or None to leave state
        unchanged.

    Raises:
        ValueError: When ``recovery_action`` is unknown or
            ``has_prior_session=True`` but ``prior_session_id`` is
            empty/None.
    """
    if recovery_action not in {"fresh", "resume", "new_session_with_id"}:
        raise ValueError(
            f"unknown recovery_action: {recovery_action!r}; "
            "expected one of 'fresh', 'resume', 'new_session_with_id'"
        )
    if has_prior_session and not (isinstance(prior_session_id, str) and prior_session_id):
        raise ValueError(
            "has_prior_session=True requires a non-empty prior_session_id"
        )

    transport_name: str
    if hasattr(transport, "value"):
        value: object = cast("object", transport.value)
        transport_name = str(value) if isinstance(value, str) else str(transport)
    else:
        transport_name = str(transport)
    resume_flag = _DEFAULT_RESUME_FLAGS.get(transport_name)
    if resume_flag is None:
        # Unknown transport: no default resume flag. Callers can still
        # pass `recovery_action='new_session_with_id'` to use a generic
        # --session-id flag, but this is unusual and only triggered by
        # custom agent configurations with a non-empty session_flag
        # template.
        resume_flag = "--session-id"

    if recovery_action == "fresh" or not has_prior_session:
        return [], None

    session_id: str = prior_session_id if isinstance(prior_session_id, str) else ""
    if recovery_action == "resume":
        return [resume_flag, session_id], session_id

    # recovery_action == "new_session_with_id"
    return ["--session-id", session_id], session_id


def _is_tool_availability_marker(failure_reason: str) -> bool:
    """Return True when the failure_reason indicates a tool-availability failure.

    Used by ``recovery_action_for_failure_reason`` to detect the new
    tool-availability family (the live wire-level ``No such tool
    available: mcp__<server>__<tool>`` error). The check is
    case-insensitive literal-substring matching on two surfaces:

    1. The exception class name is ``ToolDispatchError`` — the
       runtime-side mirror of the live error.
    2. The failure reason contains the substring
       ``"No such tool available"`` (case-insensitive). This catches
       the wire-level message format Claude Code emits.

    The literal substring is preferred over a regex match for
    performance and to match the existing
    ``_TOOL_AVAILABILITY_SUBSTRINGS`` policy in failure_classifier.
    """
    if not isinstance(failure_reason, str) or not failure_reason:
        return False
    if failure_reason == "ToolDispatchError":
        return True
    folded = failure_reason.casefold()
    return "no such tool available" in folded or "empty response with no tool calls" in folded


def recovery_action_for_failure_reason(
    failure_reason: str,
    *,
    has_prior_session: bool,
    reset_tool_registry: bool = False,
) -> str:
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
    - ``NoConversationFoundError`` family (with a prior session)
      -> ``new_session_with_id``
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
    if reset_tool_registry and _is_tool_availability_marker(failure_reason):
        return "resume"
    if failure_reason == "NoConversationFoundError":
        return "new_session_with_id"
    return "fresh"


__all__ = [
    "recovery_action_for_failure_reason",
    "resolve_session_resume_flag",
]
