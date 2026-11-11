from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from typing import Any, Final

from rich.console import Console

from ralph.display.parallel_display import phase_style
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import RALPH_THEME
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)

_CUSTOM_PIPELINE_POLICY: Final = PipelinePolicy(
    entry_phase="design",
    terminal_phase="done",
    phases={
        "design": PhaseDefinition(
            drain="design",
            role="execution",
            transitions=PhaseTransition(on_success="sign_off"),
        ),
        "sign_off": PhaseDefinition(
            drain="sign_off",
            role="commit",
            transitions=PhaseTransition(on_success="done", on_loopback="sign_off"),
        ),
        "failed_terminal": PhaseDefinition(
            drain="failed_terminal",
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(
                on_success="failed_terminal", on_loopback="failed_terminal"
            ),
        ),
        "done": PhaseDefinition(
            drain="done",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="done", on_loopback="done"),
        ),
    },
    recovery=RecoveryPolicy(failed_route="failed_terminal"),
)


def _minimal_snapshot(**kwargs: object) -> PipelineSnapshot:
    defaults: dict[str, Any] = {
        "phase": "done",
        "previous_phase": None,
        "review_issues_found": False,
        "interrupted_by_user": False,
        "last_error": None,
        "pr_url": None,
        "push_count": 0,
        "total_agent_calls": 1,
        "total_continuations": 0,
        "total_fallbacks": 0,
        "total_retries": 0,
        "workers": (),
        "prompt_path": None,
        "prompt_preview": (),
        "run_id": "test-run",
        "created_at": datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        "decision_log": (),
        "is_terminal_failure": False,
        "is_terminal_success": True,
    }
    defaults.update(kwargs)
    return PipelineSnapshot(**defaults)


def _console() -> Console:
    buf = StringIO()
    return Console(
        file=buf,
        color_system="truecolor",
        force_terminal=True,
        no_color=False,
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )


class TestPhaseStyleIsRoleDriven:
    def test_custom_execution_phase_gets_execution_style(self) -> None:
        policy = _CUSTOM_PIPELINE_POLICY
        style = phase_style("design", policy)
        assert style == "theme.phase.development"

    def test_custom_commit_phase_gets_commit_style(self) -> None:
        policy = _CUSTOM_PIPELINE_POLICY
        style = phase_style("sign_off", policy)
        assert style == "theme.phase.commit"

    def test_custom_terminal_failure_phase_gets_failed_style(self) -> None:
        policy = _CUSTOM_PIPELINE_POLICY
        style = phase_style("failed_terminal", policy)
        assert style == "theme.phase.failed"

    def test_custom_terminal_success_phase_gets_complete_style(self) -> None:
        policy = _CUSTOM_PIPELINE_POLICY
        style = phase_style("done", policy)
        assert style == "theme.phase.complete"

    def test_without_policy_canonical_name_returns_muted(self) -> None:
        style = phase_style("planning")
        assert style == "theme.text.muted"

    def test_unknown_phase_without_policy_returns_muted(self) -> None:
        style = phase_style("some_custom_phase")
        assert style == "theme.text.muted"
