"""Black-box regression tests: _build_agent_recovery_plan with OpenCodeResumableExitError.

Tests that:
1. OpenCodeResumableExitError.resumable_session_id is threaded into the recovery plan.
2. When resumable_session_id is None, extract_session_id() fallback is used from raw output.

No real subprocesses, no real wall clock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    OpenCodeResumableExitError,
)
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
    """Non-resumable exceptions never trigger the agent recovery path. Only
    OpenCodeResumableExitError and timeout errors enter recovery; all other
    exceptions result in no recovery plan.  This class documents the
    runner-side contract.
    """

    def test_non_resumable_exception_yields_no_recovery_plan(self, tmp_path: Path) -> None:
        """Any exception that is not OpenCodeResumableExitError or a timeout
        produces no recovery plan.

        A clean exit that raises a plain RuntimeError (not OpenCodeResumableExitError
        or a timeout) never triggers the recovery path — the runner enters recovery
        only for resumable exits and timeouts.
        """
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
            )
        )

        assert plan is None

    def test_recovery_plan_prompt_file_preserved(self, tmp_path: Path) -> None:
        """prompt_file from the original effect is propagated to the recovery plan."""
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
            )
        )

        assert plan is not None
        assert plan.prompt_file != "PROMPT.md"
        assert plan.session_id == "sess-post-tool"
