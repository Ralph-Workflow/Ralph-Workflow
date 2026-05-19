"""Regression tests: completion_summary derives styles from roles, not canonical phase names."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from typing import Any

from rich.console import Console

from ralph.display.completion_summary import (
    style_for_role,
)
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import RALPH_THEME
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)


def _make_custom_policy() -> PipelinePolicy:
    """Custom PipelinePolicy: design (execution), sign_off (commit), done/halt (terminal)."""
    return PipelinePolicy(
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
                transitions=PhaseTransition(on_success="done"),
            ),
            "halt": PhaseDefinition(
                drain="halt",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="halt", on_loopback="halt"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        recovery=RecoveryPolicy(failed_route="halt"),
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


class TestStyleForRole:
    def test_no_policy_returns_muted_for_role_resolution(self) -> None:
        assert style_for_role("execution", None) == "theme.text.muted"

    def test_no_policy_returns_muted_for_commit_role(self) -> None:
        assert style_for_role("commit", None) == "theme.text.muted"

    def test_no_policy_returns_muted_for_fix_role(self) -> None:
        assert style_for_role("fix", None) == "theme.text.muted"

    def test_with_policy_execution_role_returns_development_style(self) -> None:
        policy = _make_custom_policy()
        style = style_for_role("execution", policy)
        assert style == "theme.phase.development"

    def test_with_policy_commit_role_returns_commit_style(self) -> None:
        policy = _make_custom_policy()
        style = style_for_role("commit", policy)
        assert style == "theme.phase.commit"

    def test_unmatched_role_without_policy_returns_muted(self) -> None:
        assert style_for_role("unknown_role", None) == "theme.text.muted"

    def test_unmatched_role_with_policy_returns_muted(self) -> None:
        policy = _make_custom_policy()
        assert style_for_role("review", policy) == "theme.text.muted"
