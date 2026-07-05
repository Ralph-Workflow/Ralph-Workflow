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

from ralph.pipeline.effect_executor import _write_agent_retry_prompt

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
