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
