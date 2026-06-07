from __future__ import annotations

from pathlib import Path

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.pipeline.effect_executor import (
    AgentRecoveryInput,
    build_agent_recovery_plan,
    retry_prompt_file_for_context,
)
from ralph.pipeline.effects import InvokeAgentEffect


def test_retry_prompt_condenses_oversized_previous_output_excerpt(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("ship the fix", encoding="utf-8")
    huge_line = "claude result: " + ("x" * 1200)

    retry_prompt = retry_prompt_file_for_context(
        workspace_root=tmp_path,
        prompt_file=str(prompt_file),
        reason="an inactivity timeout",
        context_lines=[huge_line] * 20,
    )

    content = Path(retry_prompt).read_text(encoding="utf-8")

    assert "PREVIOUS OUTPUT SUMMARY EXCERPT:" in content
    assert "<previous log omitted>" in content
    assert huge_line not in content
    assert "claude result:" in content


def test_build_agent_recovery_plan_marks_omitted_logs_in_real_retry_flow(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("ship the fix", encoding="utf-8")
    exc = AgentInactivityTimeoutError(
        "claude",
        300.0,
        [],
        InactivityTimeoutOpts(reason=WatchdogFireReason.NO_OUTPUT_DEADLINE),
    )

    plan = build_agent_recovery_plan(
        AgentRecoveryInput(
            exc=exc,
            attempt_index=0,
            max_recovery_attempts=1,
            effect=InvokeAgentEffect(
                agent_name="claude",
                phase="development",
                prompt_file=str(prompt_file),
            ),
            workspace_root=tmp_path,
            raw_output=[],
            rendered_output=[f"claude result: {idx} " + ("x" * 400) for idx in range(20)],
            extracted_session_id=None,
            inactivity_error_type=AgentInactivityTimeoutError,
        )
    )

    assert plan is not None
    content = Path(plan.prompt_file).read_text(encoding="utf-8")

    assert "<previous log omitted>" in content
    assert " ... (truncated)" in content
