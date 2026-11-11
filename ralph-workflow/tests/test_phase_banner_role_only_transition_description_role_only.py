from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import (
    resolve_active_display,
)
from ralph.display.theme import RALPH_THEME
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)


def _make_execution_to_analysis_policy() -> PipelinePolicy:
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


class TestTransitionDescriptionRoleOnly:
    def test_renamed_phases_transition_shows_routing_not_status_prose(self) -> None:
        policy = _make_execution_to_analysis_policy()
        console = _console()
        ctx = make_display_context(console=console)
        display = resolve_active_display(None, ctx)
        display.emit_phase_transition("design", "audit", pipeline_policy=policy)
        output = console.file.getvalue()
        assert "Design" in output
        assert "Audit" in output
        assert "Work complete" not in output
        assert "analyzing results" not in output

    def test_no_phase_pair_fallback(self) -> None:
        console = _console()
        ctx = make_display_context(console=console)
        display = resolve_active_display(None, ctx)
        display.emit_phase_transition("planning", "development", pipeline_policy=None)
        output = console.file.getvalue()
        assert "Plan ready" not in output
        assert "starting development" not in output

    def test_transition_without_policy_is_minor(self) -> None:
        console = _console()
        ctx = make_display_context(
            console=console,
        )
        display = resolve_active_display(None, ctx)
        display.emit_phase_transition("planning", "development", pipeline_policy=None)
        output = console.file.getvalue()
        assert "Planning" in output
        assert "Development" in output
