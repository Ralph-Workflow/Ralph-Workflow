from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    OpenCodeResumableExitError,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.effect_executor import AgentRecoveryInput, build_agent_recovery_plan
from ralph.pipeline.effects import InvokeAgentEffect

if TYPE_CHECKING:
    from pathlib import Path


def _make_effect(
    *,
    agent_name: str = "opencode",
    phase: str = "development",
    prompt_file: str = "PROMPT.md",
) -> InvokeAgentEffect:
    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=phase,
        prompt_file=prompt_file,
    )


class TestNonResumableExceptionYieldsNoRecovery:
    def test_non_resumable_exception_yields_no_recovery_plan(self, tmp_path: Path) -> None:
        exc = RuntimeError("process completed normally")
        effect = _make_effect(phase="development")

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
                clock=FakeClock(),
            )
        )

        assert plan is None

    def test_recovery_plan_prompt_file_preserved(self, tmp_path: Path) -> None:
        exc = OpenCodeResumableExitError("opencode", session_id="sess-def")
        effect = _make_effect(prompt_file=".agent/PROMPT.md")

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
                clock=FakeClock(),
            )
        )

        assert plan is not None
        assert plan.prompt_file != ".agent/PROMPT.md"
        assert plan.session_id == "sess-def"

    def test_post_tool_empty_response_yields_recovery_plan(self, tmp_path: Path) -> None:
        exc = AgentInvocationError(
            "opencode",
            1,
            "Model returned an empty response with no tool calls",
            parsed_output=['{"type":"tool_result","tool":"read_file"}'],
        )
        effect = _make_effect(prompt_file="PROMPT.md")

        plan = build_agent_recovery_plan(
            AgentRecoveryInput(
                exc=exc,
                attempt_index=0,
                max_recovery_attempts=3,
                effect=effect,
                workspace_root=tmp_path,
                raw_output=["[plain] tool: read_file"],
                rendered_output=[],
                extracted_session_id="sess-post-tool",
                inactivity_error_type=AgentInactivityTimeoutError,
                clock=FakeClock(),
            )
        )

        assert plan is not None
        assert plan.prompt_file != "PROMPT.md"
        assert plan.session_id == "sess-post-tool"

    def test_recovery_plan_ignores_nested_tool_payload_session_id(self, tmp_path: Path) -> None:
        exc = AgentInvocationError(
            "opencode",
            1,
            "Model returned an empty response with no tool calls",
            parsed_output=['{"type":"tool_result","content":{"session_id":"tool-payload"}}'],
        )
        effect = _make_effect(prompt_file="PROMPT.md")

        plan = build_agent_recovery_plan(
            AgentRecoveryInput(
                exc=exc,
                attempt_index=0,
                max_recovery_attempts=3,
                effect=effect,
                workspace_root=tmp_path,
                raw_output=['{"type":"tool_result","content":{"session_id":"tool-payload"}}'],
                rendered_output=[],
                extracted_session_id=None,
                inactivity_error_type=AgentInactivityTimeoutError,
                clock=FakeClock(),
            )
        )

        assert plan is not None
        assert plan.session_id is None

    def test_recovery_plan_does_not_mislabel_turn_limit_as_connectivity(
        self, tmp_path: Path
    ) -> None:
        exc = AgentInvocationError(
            "nanocoder",
            1,
            "Conversation exceeded 50 turns",
            parsed_output=["Now let me inspect the timeout policy defaults."],
        )
        effect = _make_effect(prompt_file="PROMPT.md", agent_name="nanocoder")

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
                clock=FakeClock(),
            )
        )

        assert plan is not None
        assert plan.reason == "the agent conversation turn limit"
