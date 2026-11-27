from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBar, StatusBarModel, render_status_bar
from ralph.pipeline import build_integration_status
from ralph.pipeline.conflict_resolution.status import (
    conflict_status_bar_session,
    push_conflict_status_bar,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_partial_out_of_reach_status_is_present_on_conflict_entry": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown'); "
        "the same assertions now read the bar shown inside a resolution scope, because a "
        "resolver bar is a scoped overlay owned by the status bar authority and a push outside "
        "any resolution is ignored, so it can never outlive the resolution"
    ),
}


def _context(width: int = 72) -> tuple[DisplayContext, StringIO]:
    output = StringIO()
    console = Console(file=output, width=width, force_terminal=False, color_system=None)
    return make_display_context(console=console, env={}), output


def _status(cause: str = "OUT_OF_REACH"):
    return build_integration_status(
        cause=cause,
        paths_with_reasons=(("vendor/Ralph-Workflow", "gitlink conflict"),),
        command="git add -- vendor/Ralph-Workflow",
        agents_can_finish=False,
        agent_reasons=(
            "claude: cooldown unproductive_limit",
            "opencode: cooldown out_of_credits",
        ),
        done_count=3,
        remaining_count=1,
    )


def test_status_bar_wraps_complete_model_cause_and_fix_without_truncation() -> None:
    context, _output = _context(width=54)

    rendered = render_status_bar(
        StatusBarModel(
            workspace_root="/workspace",
            phase_label="Integration",
            phase_style="theme.status.error",
            attention="waiting",
            integration_status=_status(),
        ),
        context,
    ).plain

    normalized = " ".join(rendered.split())
    assert "OUT_OF_REACH" in normalized
    assert "git add -- vendor/Ralph-Workflow" in normalized
    assert "done=3; remaining=1" in normalized
    assert "..." not in normalized


def test_identical_model_emits_once_and_changed_content_emits_again() -> None:
    context, output = _context(width=120)
    bar = StatusBar(ParallelDisplay(context))

    first = StatusBarModel(
        workspace_root="/workspace",
        phase_label="Integration",
        phase_style="theme.status.error",
        attention="waiting",
        integration_status=_status(),
        elapsed_seconds=1,
    )
    bar.update(first)
    first_output = output.getvalue()
    bar.update(first)
    assert output.getvalue() == first_output

    bar.update(
        StatusBarModel(
            workspace_root="/workspace",
            phase_label="Integration",
            phase_style="theme.status.error",
            attention="waiting",
            integration_status=_status("CONFIG_FAILURE"),
            elapsed_seconds=2,
        )
    )
    assert output.getvalue() != first_output
    assert "CONFIG_FAILURE" in output.getvalue()


def test_status_exposes_path_reasons_command_and_progress() -> None:
    status = _status()

    assert status.cause == "OUT_OF_REACH"
    assert status.path_details == ("vendor/Ralph-Workflow: gitlink conflict",)
    assert status.command == "git add -- vendor/Ralph-Workflow"
    assert not status.agents_can_finish
    assert status.progress == "done=3; remaining=1"
    assert status.build_identity


def test_partial_out_of_reach_status_is_present_on_conflict_entry() -> None:
    class Display:
        def __init__(self) -> None:
            self.models: list[StatusBarModel] = []

        def update_status_bar(self, model: StatusBarModel) -> None:
            self.models.append(model)

    display = Display()
    status = _status()

    with conflict_status_bar_session(display, Path("/workspace")):
        push_conflict_status_bar(
            display,
            Path("/workspace"),
            target="main",
            round_index=1,
            round_cap=3,
            integration_status=status,
        )
        shown = display.models[-1]

    assert shown.integration_status is status
    rendered_status = shown.integration_status
    assert rendered_status is not None
    assert "vendor/Ralph-Workflow" in rendered_status.summary
    assert "agents cannot finish" not in rendered_status.summary
    assert "on cooldown" in rendered_status.summary
