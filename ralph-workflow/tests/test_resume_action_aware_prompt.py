# property-test: L — resume-aware retry prompt never inlines the original task
"""Action-aware retry prompts: the resume path must NOT re-emit the original task.

The pre-fix ``_write_agent_retry_prompt`` always inlined the FULL original task
body, so a "resumed" agent received the prompt content a second time and
restarted from the beginning (the ``I'll start by reading the current
state...`` wedge documented in PROMPT.md property L). The fix makes the
prompt construction action-aware: ``resume`` and ``new_session_with_id``
reference the original prompt by path only and append a
``CONTINUE FROM WHERE YOU LEFT OFF`` directive; ``fresh`` keeps the
inlined-body behavior for new-session retries (stale-session, new-chain).
``None`` defaults to ``fresh``-style for backward compatibility.

Black-box tests, no real subprocess, no wall clock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
)
from ralph.pipeline.effect_executor import (
    AgentRecoveryInput,
    _write_agent_retry_prompt,
    build_agent_recovery_plan,
)
from ralph.pipeline.effects import InvokeAgentEffect

_ORIGINAL_TASK_BODY_TOKEN = "ORIGINAL_TASK_BODY_TOKEN_xyz"


def _write_original_prompt(tmp_path: Path) -> Path:
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text(
        "ship the fix\n\n" + _ORIGINAL_TASK_BODY_TOKEN + "\n",
        encoding="utf-8",
    )
    return prompt


def test_write_agent_retry_prompt_resume_does_not_inline_original_task(
    tmp_path: Path,
) -> None:
    """The resume prompt must NOT contain the original task body.

    A resumed agent already has the original task in its session context;
    re-inlining it defeats resume and forces the agent to restart from
    scratch (the documented property L wedge).
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="InactivityTimeout",
        context_lines=["last line"],
        recovery_action="resume",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert _ORIGINAL_TASK_BODY_TOKEN not in content, (
        "resume prompt must NOT inline the original task body; got content: " + content
    )
    assert "Original prompt:" in content, "resume prompt must reference the original prompt by path"
    assert "continue from where you left off" in content.lower(), (
        "resume prompt must include the continue-from-where-you-left-off "
        "directive so the agent picks up the prior session"
    )


def test_write_agent_retry_prompt_fresh_inlines_original_task(
    tmp_path: Path,
) -> None:
    """The fresh prompt DOES inline the original task body (current behavior).

    Fresh-session retries (stale-session, new-chain) have no prior session
    context, so the inlined body is the only signal the agent receives. The
    new action-aware path preserves this for ``fresh``.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="fresh",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert _ORIGINAL_TASK_BODY_TOKEN in content, (
        "fresh prompt must inline the original task body (no prior session to fall back on)"
    )


def test_write_agent_retry_prompt_new_session_with_id_does_not_inline(
    tmp_path: Path,
) -> None:
    """``new_session_with_id`` references the prompt by path only.

    The third AgentRetryAction literal. The new session is annotated with
    the prior session id, so the agent has resume-like context — no need to
    re-emit the original task body.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="InactivityTimeout",
        context_lines=["last line"],
        recovery_action="new_session_with_id",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert _ORIGINAL_TASK_BODY_TOKEN not in content, (
        "new_session_with_id prompt must NOT inline the original task body"
    )
    assert "Original prompt:" in content
    assert "continue from where you left off" in content.lower()


def test_write_agent_retry_prompt_none_defaults_to_fresh_style(
    tmp_path: Path,
) -> None:
    """``recovery_action=None`` preserves today's fresh-style behavior.

    Defensive default for any caller that has not been updated. The new
    field defaults to ``None`` so existing call sites keep working; the
    prompt-side default is ``fresh``-style inline (NOT ``resume``-style)
    so no production behavior silently changes.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
    )

    content = Path(result).read_text(encoding="utf-8")
    assert _ORIGINAL_TASK_BODY_TOKEN in content, (
        "recovery_action=None must default to fresh-style (inlined body) "
        "for backward compatibility with un-updated callers"
    )


def test_resume_prompt_omits_original_task_prompt_section_marker(
    tmp_path: Path,
) -> None:
    """The resume prompt must not contain the ``ORIGINAL TASK PROMPT:`` marker.

    The current fresh-style prompt inlines the body under an
    ``ORIGINAL TASK PROMPT:`` heading. The resume prompt must omit that
    heading entirely (it would be empty), so a downstream reader can
    distinguish resume from fresh at a glance.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="InactivityTimeout",
        context_lines=["last line"],
        recovery_action="resume",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert "ORIGINAL TASK PROMPT:" not in content, (
        "resume prompt must NOT contain the 'ORIGINAL TASK PROMPT:' section "
        "heading that the fresh prompt uses to inline the body"
    )


def test_fresh_prompt_contains_original_task_prompt_section_marker(
    tmp_path: Path,
) -> None:
    """The fresh prompt carries the ``ORIGINAL TASK PROMPT:`` section heading.

    Pin the fresh-style behavior on the same heading so the two
    paths are distinguishable from content alone.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="fresh",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert "ORIGINAL TASK PROMPT:" in content


def test_resume_prompt_still_has_previous_output_summary(tmp_path: Path) -> None:
    """The resume prompt must still include the prior output summary section.

    The structural change is the ORIGINAL TASK PROMPT section (omitted in
    resume) and the resume-tail directive (added in resume). The other
    sections (error block, prior output summary) MUST be present in both
    paths so the resumed agent has the same diagnostic context.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="InactivityTimeout",
        context_lines=["prior_agent_output_line_xyz"],
        recovery_action="resume",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert "PREVIOUS OUTPUT SUMMARY EXCERPT:" in content
    assert "prior_agent_output_line_xyz" in content
    assert "ERROR RECOVERY REQUIRED" in content
    assert "Original prompt:" in content


def test_resume_prompt_recovery_action_kwarg_is_optional(
    tmp_path: Path,
) -> None:
    """``recovery_action`` is a NEW keyword argument; existing call sites still bind.

    The signature change adds the keyword with a default of ``None``;
    positional-only callers (no keyword usage) must not break. Calling
    without ``recovery_action`` falls back to fresh-style — proven by the
    parallel test for the ``None`` default. Here we only check the call
    binds cleanly without the keyword.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
    )
    assert Path(result).is_file(), "retry prompt must be written even without the keyword"


@pytest.mark.parametrize(
    "recovery_action",
    ["resume", "new_session_with_id"],
)
def test_non_fresh_actions_omit_inlined_body(tmp_path: Path, recovery_action: str) -> None:
    """Both non-fresh actions take the resume-style path (no inlined body)."""
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="InactivityTimeout",
        context_lines=["last line"],
        recovery_action=recovery_action,
    )

    content = Path(result).read_text(encoding="utf-8")
    assert _ORIGINAL_TASK_BODY_TOKEN not in content
    assert "continue from where you left off" in content.lower()


# ---------------------------------------------------------------------------
# Stale-session framing: a fresh-mode retry produced AFTER a stale-session
# failure must carry a structured STALE SESSION RECOVERY block naming the
# rejected session id, transport, and model -- so the retry agent has
# structured context instead of restarting from a generic error block.
# AC-01, AC-03, AC-05.
# ---------------------------------------------------------------------------


def test_write_agent_retry_prompt_fresh_after_stale_session_includes_stale_session_block(
    tmp_path: Path,
) -> None:
    """Fresh-mode retry AFTER stale-session failure names the rejected session id, transport, model.

    Pins the new behavior: when ``recovery_action='fresh'`` AND ``stale_session_id`` is set,
    the retry prompt carries a structured ``STALE SESSION RECOVERY`` block that names
    the rejected session id, the transport, and the model. The original task body is
    STILL inlined (fresh-style). The new block uses fresh-session framing so the retry
    agent treats the run as a fresh-session retry with prior output as starting context.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="fresh",
        stale_session_id="deadbeef-original-id",
        transport="opencode",
        model="zai-coding-plan/glm-5.2",
    )

    content = Path(result).read_text(encoding="utf-8")

    # (a) Existing fresh behavior preserved: original task body still inlined.
    assert _ORIGINAL_TASK_BODY_TOKEN in content, (
        "fresh stale-session retry must STILL inline the original task body"
    )
    # (b) Structured STALE SESSION RECOVERY header present.
    assert "STALE SESSION RECOVERY" in content, (
        "fresh stale-session retry must include the STALE SESSION RECOVERY block"
    )
    # (c) Rejected session id named.
    assert "deadbeef-original-id" in content, (
        "fresh stale-session retry must name the rejected session id"
    )
    # (d) Transport named.
    assert "opencode" in content, (
        "fresh stale-session retry must name the transport"
    )
    # (e) Model named.
    assert "zai-coding-plan/glm-5.2" in content, (
        "fresh stale-session retry must name the model"
    )
    # (f) Fresh-session framing present (NOT resume framing).
    assert "starting a FRESH session" in content, (
        "fresh stale-session retry must use fresh-session framing"
    )


def test_write_agent_retry_prompt_resume_after_stale_session_does_not_include_stale_session_block(
    tmp_path: Path,
) -> None:
    """Resume path takes precedence: stale_session_id is ignored when recovery_action='resume'.

    The resume path owns the framing via ``_resume_mode_tail``; the stale-session block
    must NOT appear in a resume prompt even when ``stale_session_id`` is set. This pins
    that the two paths do not collide.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="resume",
        stale_session_id="deadbeef-original-id",
        transport="opencode",
        model="zai-coding-plan/glm-5.2",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert "STALE SESSION RECOVERY" not in content, (
        "resume path must NOT include the STALE SESSION RECOVERY block"
    )
    assert "deadbeef-original-id" not in content, (
        "resume path must NOT name the rejected session id"
    )
    # Resume path retains its own framing.
    assert "continue from where you left off" in content.lower()


def test_write_agent_retry_prompt_fresh_without_stale_session_id_omits_stale_session_block(
    tmp_path: Path,
) -> None:
    """Defensive default: when stale_session_id is None, omit the block.

    A fresh-mode retry that is NOT triggered by a stale-session failure (e.g. transient
    connectivity) must not gain stale-session framing. The ``stale_session_id=None`` default
    guards against false-positive framing for non-stale fresh retries.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="fresh",
        # stale_session_id omitted (defaults to None)
    )

    content = Path(result).read_text(encoding="utf-8")
    assert "STALE SESSION RECOVERY" not in content, (
        "fresh retry without stale_session_id must NOT include the STALE SESSION RECOVERY block"
    )
    # Original task body still inlined (fresh behavior preserved).
    assert _ORIGINAL_TASK_BODY_TOKEN in content


def test_write_agent_retry_prompt_fresh_after_stale_session_no_double_inline_task(
    tmp_path: Path,
) -> None:
    """The STALE SESSION RECOVERY block must NOT duplicate the original task body.

    The new block is structured framing placed AFTER the original task body, never
    as a copy. The original task body is inlined EXACTLY ONCE.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="fresh",
        stale_session_id="deadbeef-original-id",
        transport="opencode",
        model="zai-coding-plan/glm-5.2",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert content.count(_ORIGINAL_TASK_BODY_TOKEN) == 1, (
        f"original task body must be inlined EXACTLY ONCE; got "
        f"{content.count(_ORIGINAL_TASK_BODY_TOKEN)} occurrences in:\n{content}"
    )


def test_write_agent_retry_prompt_fresh_after_stale_session_omits_resume_restart_directive(
    tmp_path: Path,
) -> None:
    """Fresh stale-session retry must NOT include resume-style wording.

    The STALE SESSION RECOVERY block uses fresh-session framing only. The retry agent
    must NOT see ``CONTINUE FROM WHERE YOU LEFT OFF`` or ``Do not restart from the
    beginning`` -- those phrases belong to the resume tail, not to a fresh-session
    retry. AC-05.
    """
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="fresh",
        stale_session_id="deadbeef-original-id",
        transport="opencode",
        model="zai-coding-plan/glm-5.2",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert "CONTINUE FROM WHERE YOU LEFT OFF" not in content, (
        "fresh stale-session retry must NOT include resume-style wording "
        "'CONTINUE FROM WHERE YOU LEFT OFF'"
    )
    assert "Do not restart from the beginning" not in content, (
        "fresh stale-session retry must NOT include resume-style wording "
        "'Do not restart from the beginning'"
    )


def test_build_agent_recovery_plan_fresh_non_stale_with_prior_id_omits_stale_session_block(
    tmp_path: Path,
) -> None:
    """Non-stale fresh retry with prior session id must NOT emit STALE SESSION RECOVERY.

    Pins AC-03 at the ``build_agent_recovery_plan`` boundary: stale-session
    framing is scoped STRICTLY to failures that match
    ``SESSION_NOT_FOUND_SUBSTRINGS``. A prior session id captured in
    ``AgentRecoveryInput.stale_session_id`` must NOT trigger the
    ``STALE SESSION RECOVERY`` block when the failure is non-stale (e.g.
    ``AgentInactivityTimeoutError`` with ``session_resume_safe=False``,
    ``OpenCodeResumableExitError``, generic connectivity failures).

    Reproduces the bug from the development-analysis feedback:
    ``AgentInactivityTimeoutError(session_resume_safe=False)`` plus
    ``stale_session_id='nonstale-prior-session'`` previously produced a
    prompt containing ``STALE SESSION RECOVERY`` and the false statement
    ``The previous attempt's session id `nonstale-prior-session` was
    rejected by `opencode`...`` -- even though the failure was only an
    inactivity timeout and the prior session id was never actually
    rejected. The fix gates the stale-session metadata trio on
    ``_is_stale_session_failure(exc)`` in both ``_build_recovery_input_for_attempt``
    (production path) and ``build_agent_recovery_plan`` (defense-in-depth).
    """
    prompt = _write_original_prompt(tmp_path)

    exc = AgentInactivityTimeoutError(
        "opencode", 60.0, parsed_output=["agent stalled"]
    )
    effect = InvokeAgentEffect(
        agent_name="opencode", phase="development", prompt_file=str(prompt)
    )

    plan = build_agent_recovery_plan(
        AgentRecoveryInput(
            exc=exc,
            attempt_index=0,
            max_recovery_attempts=3,
            effect=effect,
            workspace_root=tmp_path,
            raw_output=[],
            rendered_output=[],
            extracted_session_id=None,
            inactivity_error_type=AgentInactivityTimeoutError,
            stale_session_id="nonstale-prior-session",
            transport="opencode",
            model="zai-coding-plan/glm-5.2",
        )
    )

    assert plan is not None
    assert plan.recovery_action == "fresh", (
        f"non-stale inactivity timeout with no prior resumable session must "
        f"produce a fresh recovery_action; got {plan.recovery_action!r}"
    )
    # Defense-in-depth: the stale-session metadata trio on the plan must
    # be cleared when the failure was non-stale, even though the input
    # AgentRecoveryInput had stale_session_id set. This prevents the
    # downstream prompt constructor from emitting the stale-session block.
    assert plan.stale_session_id is None, (
        f"plan.stale_session_id must be None for non-stale failures; got "
        f"{plan.stale_session_id!r}"
    )
    assert plan.transport is None
    assert plan.model is None

    content = Path(plan.prompt_file).read_text(encoding="utf-8")

    # (a) The structured STALE SESSION RECOVERY block must NOT appear for
    # non-stale failures, even when stale_session_id is set on the input.
    assert "STALE SESSION RECOVERY" not in content, (
        "non-stale fresh retry with prior session id must NOT include the "
        "STALE SESSION RECOVERY block; got:\n" + content
    )
    # (b) The false claim that the prior session was rejected must NOT
    # appear -- this is the precise wedge documented in the analysis.
    assert "was rejected by" not in content, (
        "non-stale fresh retry must NOT falsely claim the prior session was rejected"
    )
    # (c) The captured prior session id must NOT appear in the prompt at
    # all (no `STALE SESSION RECOVERY` block, no other use). AC-03.
    assert "nonstale-prior-session" not in content, (
        "non-stale fresh retry must NOT name the captured prior session id "
        "anywhere in the prompt"
    )
    # (d) Fresh-mode original task body inlining must STILL happen -- the
    # stale-session gating must not break the existing fresh-mode behavior.
    assert _ORIGINAL_TASK_BODY_TOKEN in content, (
        "fresh retry must still inline the original task body"
    )


def test_build_agent_recovery_plan_fresh_true_stale_session_failure_still_emits_stale_session_block(
    tmp_path: Path,
) -> None:
    """True stale-session failure must still emit STALE SESSION RECOVERY block.

    Pins the positive case end-to-end through ``build_agent_recovery_plan``
    (not just the leaf ``_write_agent_retry_prompt``): a fresh-mode retry
    produced after an ``AgentInvocationError`` whose stderr contains a
    canonical ``SESSION_NOT_FOUND_SUBSTRINGS`` marker must carry the
    structured ``STALE SESSION RECOVERY`` block naming the rejected
    session id, transport, and model. The ``_is_stale_session_failure``
    gate must NOT regress the legitimate stale-session framing path.
    AC-01, AC-04.
    """
    prompt = _write_original_prompt(tmp_path)

    rejected_session_id = "development_analysis-c448ac22"
    exc = AgentInvocationError(
        "opencode",
        1,
        stderr=f"Error: Session not found: {rejected_session_id}",
    )
    effect = InvokeAgentEffect(
        agent_name="opencode", phase="development", prompt_file=str(prompt)
    )

    plan = build_agent_recovery_plan(
        AgentRecoveryInput(
            exc=exc,
            attempt_index=0,
            max_recovery_attempts=3,
            effect=effect,
            workspace_root=tmp_path,
            raw_output=[],
            rendered_output=[],
            extracted_session_id=None,
            inactivity_error_type=AgentInactivityTimeoutError,
            stale_session_id=rejected_session_id,
            transport="opencode",
            model="zai-coding-plan/glm-5.2",
        )
    )

    assert plan is not None
    assert plan.recovery_action == "fresh"
    # The stale-session metadata trio must survive into the plan when the
    # failure was actually a stale-session failure.
    assert plan.stale_session_id == rejected_session_id
    assert plan.transport == "opencode"
    assert plan.model == "zai-coding-plan/glm-5.2"

    content = Path(plan.prompt_file).read_text(encoding="utf-8")
    assert "STALE SESSION RECOVERY" in content
    assert rejected_session_id in content
    assert "opencode" in content
    assert "zai-coding-plan/glm-5.2" in content
    # Fresh-mode original task body inlining preserved.
    assert _ORIGINAL_TASK_BODY_TOKEN in content
