"""Black-box regression tests: _build_agent_recovery_plan with OpenCodeResumableExitError.

Tests that:
1. OpenCodeResumableExitError.resumable_session_id is threaded into the recovery plan.
2. When resumable_session_id is None, extract_session_id() fallback is used from raw output.

No real subprocesses, no real wall clock.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.invoke import AgentInactivityTimeoutError, OpenCodeResumableExitError
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


class TestBuildAgentRecoveryPlanInteractiveClaude:
    def test_resumable_session_id_is_threaded_into_recovery_plan(self, tmp_path: Path) -> None:
        """Claude interactive resumable exit threads session_id into the recovery plan."""
        exc = OpenCodeResumableExitError("claude", session_id="sess-abc")
        effect = _make_effect(agent_name="claude")

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

        assert plan is not None, "Expected a recovery plan, got None"
        assert plan.session_id == "sess-abc"
        assert plan.prompt_file != "PROMPT.md"

    def test_fallback_to_extract_session_id_when_resumable_session_id_is_none(
        self, tmp_path: Path
    ) -> None:
        """Claude interactive fallback extracts the session id from raw output."""
        exc = OpenCodeResumableExitError("claude", session_id=None)
        effect = _make_effect(agent_name="claude")
        raw_output = [json.dumps({"session_id": "sess-from-output"})]

        plan = build_agent_recovery_plan(
            AgentRecoveryInput(
                exc=exc,
                attempt_index=0,
                max_recovery_attempts=3,
                effect=effect,
                workspace_root=tmp_path,
                raw_output=raw_output,
                rendered_output=[],
                extracted_session_id="sess-from-output",
                inactivity_error_type=AgentInactivityTimeoutError,
            )
        )

        assert plan is not None, "Expected a recovery plan, got None"
        assert plan.session_id == "sess-from-output"
        assert plan.prompt_file != "PROMPT.md"

    def test_no_plan_when_attempt_limit_exceeded(self, tmp_path: Path) -> None:
        """No recovery plan for Claude interactive when attempt limit is exceeded."""
        exc = OpenCodeResumableExitError("claude", session_id="sess-xyz")
        effect = _make_effect(agent_name="claude")

        plan = build_agent_recovery_plan(
            AgentRecoveryInput(
                exc=exc,
                attempt_index=3,
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
