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

R7 root-cause diagnostic surface (NEW):

    In addition to ``resumable_session_id`` (the resume hook), the
    exception carries four NEW keyword-only diagnostic attributes
    that capture the watchdog state at the moment of the rc=0 exit.
    The four attributes are preserved on the exception for
    programmatic access (an on-call operator reads them via
    ``exc.last_observed_tool_call``, ``exc.last_evidence_summary``,
    ``exc.elapsed_seconds``, ``exc.transcript_tail``) without
    walking the exception chain or re-invoking the watchdog.

    Diagnostic attributes (all keyword-only with default ``None`` /
    ``()`` so legacy two-arg callers are unaffected):

      * ``last_observed_tool_call``: ``str | None`` -- the most recent
        parsed tool-call verb (e.g. ``"read_file"``,
        ``"tool_use:Edit"``) from the line reader layer. ``None`` if
        no tool call was recorded. SURFACED IN MESSAGE.
      * ``last_evidence_summary``: ``str | None`` -- the watchdog's
        ``last_evidence_summary(now).to_dict_list()`` str-coerced
        payload (the per-channel evidence summary at the moment
        of the exit). ``None`` if no evidence summary was captured.
        PROGRAMMATIC-ONLY: not rendered into the exception message
        to avoid unbounded message size from a verbose evidence
        payload.
      * ``elapsed_seconds``: ``float | None`` -- the watchdog's
        ``idle_elapsed_seconds(clock.monotonic())`` value at the
        moment of the exit. ``None`` if no elapsed value was
        captured. SURFACED IN MESSAGE.
      * ``transcript_tail``: ``tuple[str, ...]`` -- the last 10
        lines of the bounded output transcript at the moment of the
        exit (hard-capped via tuple slice in the line-reader
        construction site). Default ``()`` for legacy callers.
        PROGRAMMATIC-ONLY: not rendered into the exception message
        to avoid unbounded message size from a long transcript.

    The exception message embeds ONLY the two bounded-size
    diagnostic fields -- ``last_observed_tool_call`` and
    ``elapsed_seconds`` -- so a logged traceback remains concise
    and human-readable. The full four-attribute surface is preserved
    on the exception for callers that need the verbose evidence
    summary or transcript tail. Format:

        ``OpenCodeResumableExitError: Agent 'opencode' failed
        with code 0: agent session exited without required
        completion evidence (no artifact, no declare_complete)
        [last_tool_call=read_file, elapsed=420.0s]``

    The ``[last_tool_call=..., elapsed=...]`` suffix is omitted when
    both diagnostic attributes are ``None`` (legacy callers see the
    original message unchanged); a partial suffix is rendered when
    exactly one attribute is set. ``last_evidence_summary`` and
    ``transcript_tail`` are NEVER rendered into the message -- they
    are programmatic-only by design (see per-attribute notes above).

Lock-in regression test:
    ``tests/recovery/test_opencode_resumable_exit_classification.py``
    proves the four contract points above. The NEW diagnostic
    surface is pinned by ``tests/recovery/test_opencode_resumable_exit_classifier.py``
    tests ``test_diagnostic_context_carried`` and
    ``test_backward_compatible_construction``.

References:
    - ``ralph/agents/invoke/_session_resume.py`` for the
      recovery-action contract.
    - ``ralph/recovery/failure_classifier.py:_categorize_exc`` for
      the typed-cause branch ordering.
    - ``ralph/agents/invoke/_errors.py`` for the ``AgentInvocationError``
      base class hierarchy.
    - ``ralph/agents/invoke/_completion.py:_CompletionCheckOptions``
      for the dataclass that threads the diagnostic fields.
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

    R7 root-cause diagnostic surface (NEW): the exception also carries
    four keyword-only diagnostic attributes -- ``last_observed_tool_call``,
    ``last_evidence_summary``, ``elapsed_seconds``, and
    ``transcript_tail`` -- that capture the watchdog state at the
    moment of the rc=0 exit. The four attributes are preserved on
    the exception for programmatic access; the two bounded-size
    fields (``last_observed_tool_call`` and ``elapsed_seconds``) are
    ALSO surfaced in the exception message so a logged traceback is
    actionable. The verbose fields (``last_evidence_summary`` and
    ``transcript_tail``) are programmatic-only by design -- not
    rendered into the message to avoid unbounded message size.
    """

    def __init__(
        self,
        agent_name: str,
        session_id: str | None = None,
        *,
        last_observed_tool_call: str | None = None,
        last_evidence_summary: str | None = None,
        elapsed_seconds: float | None = None,
        transcript_tail: tuple[str, ...] = (),
    ) -> None:
        self.resumable_session_id = session_id
        self.last_observed_tool_call = last_observed_tool_call
        self.last_evidence_summary = last_evidence_summary
        self.elapsed_seconds = elapsed_seconds
        self.transcript_tail = transcript_tail
        base_message = (
            "agent session exited without required completion evidence "
            "(no artifact, no declare_complete)"
        )
        diagnostic_suffix_parts: list[str] = []
        if last_observed_tool_call is not None:
            diagnostic_suffix_parts.append(f"last_tool_call={last_observed_tool_call}")
        if elapsed_seconds is not None:
            diagnostic_suffix_parts.append(f"elapsed={round(elapsed_seconds, 1)}s")
        if diagnostic_suffix_parts:
            full_message = base_message + " [" + ", ".join(diagnostic_suffix_parts) + "]"
        else:
            full_message = base_message
        super().__init__(agent_name, 0, full_message)


__all__ = ["OpenCodeResumableExitError"]
