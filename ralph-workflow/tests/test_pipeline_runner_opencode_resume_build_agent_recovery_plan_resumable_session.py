"""Black-box regression tests: _build_agent_recovery_plan with OpenCodeResumableExitError.

Tests that:
1. OpenCodeResumableExitError.resumable_session_id is threaded into the recovery plan.
2. When resumable_session_id is None, extract_session_id() fallback is used from raw output.
3. The resolved ``recovery_action`` is carried on the returned ``AgentRecoveryPlan``
   so the prompt constructor can branch on it (resume tail vs fresh inline).
4. End-to-end: the production subprocess reader raises a resume-safe
   ``AgentInactivityTimeoutError`` AND ``build_agent_recovery_plan`` maps it
   to ``recovery_action='resume'`` with the right session id (proves the
   new wiring in ``_process_reader.py`` feeds through to the plan).

No real subprocesses, no real wall clock.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Literal

import pytest

from ralph.agents import invoke as invoke_module
from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InvokeOptions,
    OpenCodeResumableExitError,
    invoke_agent,
)
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig
from ralph.pipeline.effect_executor import AgentRecoveryInput, build_agent_recovery_plan
from ralph.pipeline.effects import InvokeAgentEffect
from tests.agents.test_invoke_timeout_integration_helper__waitingstrategy import _WaitingStrategy

if TYPE_CHECKING:
    from collections.abc import Iterator
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

    def test_opencode_resumable_exit_yields_resume_action(self, tmp_path: Path) -> None:
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

    def test_inactivity_with_resume_opts_yields_resume_action(self, tmp_path: Path) -> None:
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

    def test_inactivity_without_resume_opts_yields_fresh_action(self, tmp_path: Path) -> None:
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
        exc = AgentInvocationError("claude", 1, "No conversation found with session ID: stale-x")
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

    def test_opencode_stale_session_substring_routes_to_fresh(self, tmp_path: Path) -> None:
        """OpenCode stale-session variant: 'Session not found: <id>' -> 'fresh'."""
        exc = AgentInvocationError("opencode", 1, "Session not found: opencode-stale-id")
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

    def test_subprocess_reader_raises_resume_safe_then_plan_uses_resume_action(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """End-to-end: production subprocess reader -> recovery plan builder.

        The full chain drives the actual production code path: the
        ``invoke_agent`` seam (monkeypatched at ``subprocess.Popen``) raises
        an ``AgentInactivityTimeoutError`` whose
        ``session_resume_safe=True`` and ``resumable_session_id`` are
        populated by the new ``_process_reader.py`` wiring, and
        ``build_agent_recovery_plan`` maps that to
        ``recovery_action='resume'`` with the right session id.

        Pre-fix (or a future regression of Step 4) the subprocess reader
        raised with ``session_resume_safe=False`` and
        ``resumable_session_id=None``; the recovery plan builder would
        then return ``recovery_action='fresh'`` and ``session_id=None``,
        defeating resume and producing the restart-from-scratch wedge.

        Updated for the AC-03 contract: drives a resumable reason
        (``NO_OUTPUT_DEADLINE`` via an ``_ActiveStrategy``) instead of
        ``CHILDREN_PERSIST_TOO_LONG`` (NOT resumable per the new
        contract).  The session id is captured from the agent's
        stdout stream so the test exercises the captured-id path
        rather than the expected-id fallback.
        """
        config = AgentConfig(cmd="opencode", output_flag="--json-stream")
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("hello", encoding="utf-8")

        class _FakeProcess:
            pid: int = 12345

            def __init__(self) -> None:
                self._gate = threading.Event()
                self._gate.set()
                self.stdout = self._stdout_iter()
                self.stderr = self._stderr()
                self.returncode: int | None = 0
                self.terminated = False

            def _stdout_iter(self) -> Iterator[str]:
                yield '{"type":"session","session_id":"sess-from-subprocess"}\n'
                self._gate.clear()
                self._gate.wait(timeout=5.0)
                yield from ()

            @staticmethod
            def _stderr() -> object:
                class _Stderr:
                    @staticmethod
                    def read() -> str:
                        return ""

                return _Stderr()

            def poll(self) -> int | None:
                return self.returncode

            def __enter__(self) -> _FakeProcess:
                return self

            def __exit__(
                self,
                _exc_type: object,
                _exc: object,
                _tb: object,
            ) -> Literal[False]:
                return False

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return self.returncode

            def terminate(self) -> None:
                self.terminated = True
                self.returncode = -15

            def kill(self) -> None:
                self.terminated = True
                self.returncode = -9

        proc = _FakeProcess()

        class _ActiveStrategy(_WaitingStrategy):
            """Active classifier so NO_OUTPUT_DEADLINE fires (the
            resumable path).  Mirrors the pattern used by
            ``tests/test_subprocess_reader_resume_safe.py`` for
            the same reason.
            """

            def classify_quiet(
                self,
                handle: object,
                liveness_probe: object,
            ) -> AgentExecutionState:
                return AgentExecutionState.ACTIVE

        monkeypatch.setattr(
            invoke_module,
            "strategy_for_command",
            lambda *args, **kwargs: _ActiveStrategy(),
        )
        monkeypatch.setattr(
            "ralph.agents.invoke.subprocess.Popen",
            lambda *args, **kwargs: proc,
        )
        monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

        clock = FakeClock()
        opts = InvokeOptions(
            show_progress=False,
            workspace_path=tmp_path,
            idle_timeout_seconds=0.05,
            max_waiting_on_child_seconds=10.0,
            max_session_seconds=None,
            max_waiting_on_child_no_progress_seconds=None,
            waiting_status_interval_seconds=100.0,
            idle_poll_interval_seconds=0.01,
        )

        try:
            with pytest.raises(AgentInactivityTimeoutError) as exc_info:
                list(
                    invoke_agent(
                        config,
                        str(prompt_file),
                        options=opts,
                        _clock=clock,
                    )
                )
        finally:
            proc._gate.set()

        assert exc_info.value.session_resume_safe is True
        assert exc_info.value.resumable_session_id == "sess-from-subprocess"
        assert exc_info.value.reason == WatchdogFireReason.NO_OUTPUT_DEADLINE

        effect = _make_effect(agent_name="opencode")
        plan = build_agent_recovery_plan(
            AgentRecoveryInput(
                exc=exc_info.value,
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
        assert plan.session_id == "sess-from-subprocess"
