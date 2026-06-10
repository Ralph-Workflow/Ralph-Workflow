"""Black-box regression tests: _build_agent_recovery_plan with OpenCodeResumableExitError.

Tests that:
1. OpenCodeResumableExitError.resumable_session_id is threaded into the recovery plan.
2. When resumable_session_id is None, extract_session_id() fallback is used from raw output.
3. The resolved ``recovery_action`` is carried on the returned ``AgentRecoveryPlan``
   so the prompt constructor can branch on it (resume tail vs fresh inline).

No real subprocesses, no real wall clock.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    OpenCodeResumableExitError,
)
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
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


class TestBuildAgentRecoveryPlanResumableSession:
    def test_resumable_session_id_is_threaded_into_recovery_plan(self, tmp_path: Path) -> None:
        """OpenCodeResumableExitError with session_id yields plan.session_id == that id."""
        exc = OpenCodeResumableExitError("opencode", session_id="sess-abc")
        effect = _make_effect()

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
        """When resumable_session_id is None, plan picks up session id from raw NDJSON output."""
        exc = OpenCodeResumableExitError("opencode", session_id=None)
        effect = _make_effect()
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
        """No recovery plan when attempt_index >= max_recovery_attempts."""
        exc = OpenCodeResumableExitError("opencode", session_id="sess-xyz")
        effect = _make_effect()

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

    def test_opencode_textual_session_flag_extracted_via_extracted_session_id(
        self, tmp_path: Path
    ) -> None:
        """OpenCode textual --session flag value is recognized when passed as extracted_session_id.

        This regression test verifies that when OpenCode emits raw output containing
        '--session <id>' and that id is extracted by extract_session_id() in the caller
        (effect_executor.py), the recovered session id is properly threaded into
        the AgentRecoveryInput and used in the recovery plan.
        """
        exc = OpenCodeResumableExitError("opencode", session_id=None)
        effect = _make_effect()
        # Raw output contains OpenCode textual --session flag
        raw_output = ["Some output", "  --session sess-from-opencode-text", "Another line"]

        plan = build_agent_recovery_plan(
            AgentRecoveryInput(
                exc=exc,
                attempt_index=0,
                max_recovery_attempts=3,
                effect=effect,
                workspace_root=tmp_path,
                raw_output=raw_output,
                rendered_output=[],
                # Simulate what effect_executor.py does: extract_session_id(tuple(raw_output))
                extracted_session_id="sess-from-opencode-text",
                inactivity_error_type=AgentInactivityTimeoutError,
            )
        )

        assert plan is not None, "Expected a recovery plan, got None"
        assert plan.session_id == "sess-from-opencode-text"
        assert plan.prompt_file != "PROMPT.md"


class TestBuildAgentRecoveryPlanCarriesRecoveryAction:
    """The resolved ``recovery_action`` MUST be carried on the returned plan.

    The new plumbing threads ``recovery_action`` through
    ``build_agent_recovery_plan`` so the prompt constructor can branch on
    it (resume-style tail vs fresh-style inline). The matrix below pins
    the four canonical cases:

    - OpenCode resumable exit (with session id) -> ``resume``
    - Inactivity timeout WITH ``InactivityTimeoutOpts(resume_safe=True)``
      and a ``resumable_session_id`` -> ``resume`` (and the session id is
      threaded into the plan)
    - Inactivity timeout WITHOUT opts (the default ``session_resume_safe=False``)
      -> ``fresh`` and ``session_id is None``
    - ``AgentInvocationError`` carrying a stale-session substring ("No
      conversation found with session ID:" / "Session not found") -> ``fresh``
      and ``session_id is None`` (the canonical SESSION_NOT_FOUND family)
    """

    def test_opencode_resumable_exit_yields_resume_action(
        self, tmp_path: Path
    ) -> None:
        """OpenCodeResumableExitError with session id -> recovery_action='resume'."""
        exc = OpenCodeResumableExitError("opencode", session_id="sess-x")
        effect = _make_effect()

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
        assert plan.recovery_action == "resume"
        assert plan.session_id == "sess-x"

    def test_inactivity_with_resume_opts_yields_resume_action(
        self, tmp_path: Path
    ) -> None:
        """Inactivity timeout with session_resume_safe=True -> 'resume'.

        The plan's session_id is the ``resumable_session_id`` from the
        InactivityTimeoutOpts, proving the path through
        ``_resolve_recovery_session_id`` returned a non-None value
        (i.e. ``_failure_requires_fresh_session`` returned False because
        ``session_resume_safe=True``).
        """
        exc = AgentInactivityTimeoutError(
            "claude",
            300.0,
            [],
            InactivityTimeoutOpts(
                reason=WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
                session_resume_safe=True,
                resumable_session_id="sess-y",
            ),
        )
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

        assert plan is not None
        assert plan.recovery_action == "resume"
        assert plan.session_id == "sess-y"

    def test_inactivity_without_resume_opts_yields_fresh_action(
        self, tmp_path: Path
    ) -> None:
        """Inactivity timeout WITHOUT opts (session_resume_safe=False default) -> 'fresh'."""
        exc = AgentInactivityTimeoutError("claude", 300.0)
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

        assert plan is not None
        assert plan.recovery_action == "fresh"
        assert plan.session_id is None

    def test_stale_session_substring_routes_to_fresh(self, tmp_path: Path) -> None:
        """AgentInvocationError with a stale-session substring -> 'fresh', no session.

        Canonical SESSION_NOT_FOUND case used in
        ``tests/test_phases_retry_on_stale_session.py``:
        ``AgentInvocationError('claude', 1, 'No conversation found with session ID: stale-x')``.
        """
        exc = AgentInvocationError(
            "claude", 1, "No conversation found with session ID: stale-x"
        )
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

        assert plan is not None
        assert plan.recovery_action == "fresh"
        assert plan.session_id is None

    def test_opencode_stale_session_substring_routes_to_fresh(
        self, tmp_path: Path
    ) -> None:
        """OpenCode stale-session variant: 'Session not found: <id>' -> 'fresh'."""
        exc = AgentInvocationError(
            "opencode", 1, "Session not found: opencode-stale-id"
        )
        effect = _make_effect(agent_name="opencode")

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
        assert plan.recovery_action == "fresh"
        assert plan.session_id is None

    def test_no_plan_when_attempt_limit_exceeded_carries_recovery_action(
        self, tmp_path: Path
    ) -> None:
        """When the attempt cap is exceeded, no plan is returned.

        The new ``recovery_action`` field must not appear on a None plan
        (and the test should not raise; this pins the early-return
        behavior).
        """
        exc = OpenCodeResumableExitError("opencode", session_id="sess-cap")
        effect = _make_effect()

        plan = build_agent_recovery_plan(
            AgentRecoveryInput(
                exc=exc,
                attempt_index=5,
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
