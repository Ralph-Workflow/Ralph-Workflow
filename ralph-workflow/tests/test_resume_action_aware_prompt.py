from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
)
from ralph.agents.timeout_clock import FakeClock
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


@pytest.mark.timeout_seconds(2.0)
def test_write_agent_retry_prompt_none_defaults_to_fresh_style(
    tmp_path: Path,
) -> None:
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


def test_write_agent_retry_prompt_fresh_after_stale_session_includes_stale_session_block(
    tmp_path: Path,
) -> None:
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

    assert _ORIGINAL_TASK_BODY_TOKEN in content, (
        "fresh stale-session retry must STILL inline the original task body"
    )
    assert "STALE SESSION RECOVERY" in content, (
        "fresh stale-session retry must include the STALE SESSION RECOVERY block"
    )
    assert "deadbeef-original-id" in content, (
        "fresh stale-session retry must name the rejected session id"
    )
    assert "opencode" in content, "fresh stale-session retry must name the transport"
    assert "zai-coding-plan/glm-5.2" in content, "fresh stale-session retry must name the model"
    assert "starting a FRESH session" in content, (
        "fresh stale-session retry must use fresh-session framing"
    )


def test_write_agent_retry_prompt_resume_after_stale_session_does_not_include_stale_session_block(
    tmp_path: Path,
) -> None:
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
    assert "continue from where you left off" in content.lower()


def test_write_agent_retry_prompt_fresh_without_stale_session_id_omits_stale_session_block(
    tmp_path: Path,
) -> None:
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=["last line"],
        recovery_action="fresh",
    )

    content = Path(result).read_text(encoding="utf-8")
    assert "STALE SESSION RECOVERY" not in content, (
        "fresh retry without stale_session_id must NOT include the STALE SESSION RECOVERY block"
    )
    assert _ORIGINAL_TASK_BODY_TOKEN in content


def test_write_agent_retry_prompt_fresh_after_stale_session_no_double_inline_task(
    tmp_path: Path,
) -> None:
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
    prompt = _write_original_prompt(tmp_path)

    exc = AgentInactivityTimeoutError("opencode", 60.0, parsed_output=["agent stalled"])
    effect = InvokeAgentEffect(agent_name="opencode", phase="development", prompt_file=str(prompt))

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
            clock=FakeClock(),
        )
    )

    assert plan is not None
    assert plan.recovery_action == "fresh", (
        f"non-stale inactivity timeout with no prior resumable session must "
        f"produce a fresh recovery_action; got {plan.recovery_action!r}"
    )
    assert plan.stale_session_id is None, (
        f"plan.stale_session_id must be None for non-stale failures; got {plan.stale_session_id!r}"
    )
    assert plan.transport is None
    assert plan.model is None

    content = Path(plan.prompt_file).read_text(encoding="utf-8")

    assert "STALE SESSION RECOVERY" not in content, (
        "non-stale fresh retry with prior session id must NOT include the "
        "STALE SESSION RECOVERY block; got:\n" + content
    )
    assert "was rejected by" not in content, (
        "non-stale fresh retry must NOT falsely claim the prior session was rejected"
    )
    assert "nonstale-prior-session" not in content, (
        "non-stale fresh retry must NOT name the captured prior session id anywhere in the prompt"
    )
    assert _ORIGINAL_TASK_BODY_TOKEN in content, (
        "fresh retry must still inline the original task body"
    )


def test_build_agent_recovery_plan_fresh_true_stale_session_failure_still_emits_stale_session_block(
    tmp_path: Path,
) -> None:
    prompt = _write_original_prompt(tmp_path)

    rejected_session_id = "development_analysis-c448ac22"
    exc = AgentInvocationError(
        "opencode",
        1,
        stderr=f"Error: Session not found: {rejected_session_id}",
    )
    effect = InvokeAgentEffect(agent_name="opencode", phase="development", prompt_file=str(prompt))

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
            clock=FakeClock(),
        )
    )

    assert plan is not None
    assert plan.recovery_action == "fresh"
    assert plan.stale_session_id == rejected_session_id
    assert plan.transport == "opencode"
    assert plan.model == "zai-coding-plan/glm-5.2"

    content = Path(plan.prompt_file).read_text(encoding="utf-8")
    assert "STALE SESSION RECOVERY" in content
    assert rejected_session_id in content
    assert "opencode" in content
    assert "zai-coding-plan/glm-5.2" in content
    assert _ORIGINAL_TASK_BODY_TOKEN in content


def test_write_agent_retry_prompt_fresh_after_stale_session_strengthens_source_of_truth(
    tmp_path: Path,
) -> None:
    prompt = _write_original_prompt(tmp_path)

    result = _write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt),
        reason="StaleSession",
        context_lines=[],
        recovery_action="fresh",
        stale_session_id="opencode-stale-empty",
        transport="opencode",
        model="m",
    )

    content = Path(result).read_text(encoding="utf-8")

    assert "INFORMATIONAL ONLY" in content, (
        "strengthened stale-session retry prompt must include the "
        "'INFORMATIONAL ONLY' substring; got:\n" + content
    )
    assert "ONLY source of truth" in content, (
        "strengthened stale-session retry prompt must include the "
        "'ONLY source of truth' substring; got:\n" + content
    )
    assert "FRESH attempt" in content, (
        "strengthened stale-session retry prompt must include the "
        "'FRESH attempt' substring; got:\n" + content
    )
    assert "rejected the prior session id BEFORE" in content, (
        "strengthened stale-session retry prompt must include the "
        "'rejected the prior session id BEFORE' substring; got:\n" + content
    )

    assert "`opencode-stale-empty`" in content, (
        "strengthened stale-session retry prompt must keep the backticked session id"
    )
    assert "opencode" in content, (
        "strengthened stale-session retry prompt must keep the transport label"
    )
    assert "(model=m)" in content, (
        "strengthened stale-session retry prompt must keep the (model=...) label"
    )
    assert "STALE SESSION RECOVERY" in content, (
        "strengthened stale-session retry prompt must keep the STALE SESSION RECOVERY header"
    )
