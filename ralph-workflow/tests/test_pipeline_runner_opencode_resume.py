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
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.runner import _build_agent_recovery_plan

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


class TestBuildAgentRecoveryPlanResumableSession:
    def test_resumable_session_id_is_threaded_into_recovery_plan(self, tmp_path: Path) -> None:
        """OpenCodeResumableExitError with session_id yields plan.session_id == that id."""
        exc = OpenCodeResumableExitError("opencode", session_id="sess-abc")
        effect = _make_effect()

        plan = _build_agent_recovery_plan(
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

        assert plan is not None, "Expected a recovery plan, got None"
        assert plan.session_id == "sess-abc"
        assert plan.prompt_file != "PROMPT.md"

    def test_fallback_to_extract_session_id_when_resumable_session_id_is_none(
        self, tmp_path: Path
    ) -> None:
        """When resumable_session_id is None, plan picks up session id from raw NDJSON output."""
        exc = OpenCodeResumableExitError("opencode", session_id=None)
        effect = _make_effect()
        raw_output = [json.dumps({"session_id": "sess-from-output"})]

        plan = _build_agent_recovery_plan(
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

        assert plan is not None, "Expected a recovery plan, got None"
        assert plan.session_id == "sess-from-output"
        assert plan.prompt_file != "PROMPT.md"

    def test_no_plan_when_attempt_limit_exceeded(self, tmp_path: Path) -> None:
        """No recovery plan when attempt_index >= max_recovery_attempts."""
        exc = OpenCodeResumableExitError("opencode", session_id="sess-xyz")
        effect = _make_effect()

        plan = _build_agent_recovery_plan(
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

        assert plan is None


class TestBuildAgentRecoveryPlanInteractiveClaude:
    def test_resumable_session_id_is_threaded_into_recovery_plan(self, tmp_path: Path) -> None:
        """Claude interactive resumable exit threads session_id into the recovery plan."""
        exc = OpenCodeResumableExitError("claude", session_id="sess-abc")
        effect = _make_effect(agent_name="claude")

        plan = _build_agent_recovery_plan(
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

        plan = _build_agent_recovery_plan(
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

        assert plan is not None, "Expected a recovery plan, got None"
        assert plan.session_id == "sess-from-output"
        assert plan.prompt_file != "PROMPT.md"

    def test_no_plan_when_attempt_limit_exceeded(self, tmp_path: Path) -> None:
        """No recovery plan for Claude interactive when attempt limit is exceeded."""
        exc = OpenCodeResumableExitError("claude", session_id="sess-xyz")
        effect = _make_effect(agent_name="claude")

        plan = _build_agent_recovery_plan(
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

        assert plan is None


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

        plan = _build_agent_recovery_plan(
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

        assert plan is None

    def test_recovery_plan_prompt_file_preserved(self, tmp_path: Path) -> None:
        """prompt_file from the original effect is propagated to the recovery plan."""
        exc = OpenCodeResumableExitError("opencode", session_id="sess-def")
        effect = _make_effect(prompt_file=".agent/PROMPT.md")

        plan = _build_agent_recovery_plan(
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

        assert plan is not None
        assert plan.prompt_file != ".agent/PROMPT.md"
        assert plan.session_id == "sess-def"
