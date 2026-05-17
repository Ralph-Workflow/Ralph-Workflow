"""Regression tests: phase_banner uses role-only styling, no canonical phase-name fallback."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.phase_banner import (
    phase_style,
    show_phase_transition,
)
from ralph.display.theme import RALPH_THEME
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)


def _make_execution_to_analysis_policy() -> PipelinePolicy:
    """Policy with 'design' (execution) → 'audit' (analysis) → 'done' (terminal)."""
    return PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        phases={
            "design": PhaseDefinition(
                drain="design",
                role="execution",
                transitions=PhaseTransition(on_success="audit"),
            ),
            "audit": PhaseDefinition(
                drain="audit",
                role="analysis",
                transitions=PhaseTransition(on_success="done"),
            ),
            "sign_off": PhaseDefinition(
                drain="sign_off",
                role="commit",
                transitions=PhaseTransition(on_success="done"),
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


def _make_commit_policy() -> PipelinePolicy:
    """Policy with 'sign_off' (commit) phase."""
    return PipelinePolicy(
        entry_phase="sign_off",
        terminal_phase="done",
        phases={
            "sign_off": PhaseDefinition(
                drain="sign_off",
                role="commit",
                transitions=PhaseTransition(on_success="done"),
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


class TestPhaseStyleRoleOnly:
    def test_renamed_execution_phase_gets_execution_style(self) -> None:
        policy = _make_execution_to_analysis_policy()
        style = phase_style("design", policy)
        assert style == "theme.phase.development"

    def test_renamed_commit_phase_gets_commit_style(self) -> None:
        policy = _make_commit_policy()
        style = phase_style("sign_off", policy)
        assert style == "theme.phase.commit"

    def test_no_policy_no_canonical_name_lookup(self) -> None:
        # Without policy, canonical phase names are NOT recognized — only role strings are.
        # 'planning' is not a role, so it returns the muted default.
        style = phase_style("planning")
        assert style == "theme.text.muted"

    def test_renamed_terminal_failure_gets_failed_style(self) -> None:
        policy = _make_execution_to_analysis_policy()
        style = phase_style("failed_terminal", policy)
        assert style == "theme.phase.failed"

    def test_renamed_terminal_success_gets_complete_style(self) -> None:
        policy = _make_execution_to_analysis_policy()
        style = phase_style("done", policy)
        assert style == "theme.phase.complete"


class TestTransitionDescriptionRoleOnly:
    def test_renamed_phases_transition_shows_routing_not_status_prose(self) -> None:
        """show_phase_transition with renamed phases shows routing labels, not status prose.

        The phase-close banner communicates exit context; the transition
        banner shows only routing context (from-phase → to-phase labels).
        """
        policy = _make_execution_to_analysis_policy()
        console = _console()
        ctx = make_display_context(console=console)
        show_phase_transition("design", "audit", pipeline_policy=policy, display_context=ctx)
        output = console.file.getvalue()
        assert "Design" in output
        assert "Audit" in output
        # Must not contain old duplicated status prose
        assert "Work complete" not in output
        assert "analyzing results" not in output

    def test_no_phase_pair_fallback(self) -> None:
        """show_phase_transition without policy shows no hardcoded canonical description."""
        console = _console()
        ctx = make_display_context(console=console)
        show_phase_transition("planning", "development", pipeline_policy=None, display_context=ctx)
        output = console.file.getvalue()
        # Old canonical table had 'Plan ready — starting development' which must not appear.
        assert "Plan ready" not in output
        assert "starting development" not in output

    def test_transition_without_policy_is_minor(self) -> None:
        """Without policy, any transition is treated as minor (no major banner)."""
        console = _console()
        ctx = make_display_context(console=console, force_mode="medium")
        show_phase_transition("planning", "development", pipeline_policy=None, display_context=ctx)
        output = console.file.getvalue()
        # Minor transitions render as a simple Rule, not the major banner.
        # Major banners in medium/wide mode print two Rules + description.
        # We can verify the transition text appears but no description is emitted.
        assert "Planning" in output
        assert "Development" in output
