from io import StringIO

from rich.console import Console

from ralph.display import status
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME

_PROGRESS_COMPLETED = 2
_PROGRESS_TOTAL = 5


def test_display_phase_shows_phase_and_iteration() -> None:
    console = Console(record=True, theme=RALPH_THEME)
    ctx = make_display_context(console=console)
    status.display_phase("Planning", iteration=1, total=3, display_context=ctx)

    output = console.export_text()

    assert "Phase: Planning" in output
    assert "Iteration 1 of 3" in output


def test_display_progress_sets_task_state() -> None:
    console = Console(record=True, theme=RALPH_THEME)
    ctx = make_display_context(console=console)
    progress = status.display_progress(
        current=_PROGRESS_COMPLETED,
        total=_PROGRESS_TOTAL,
        phase="Execution",
        display_context=ctx,
    )

    assert len(progress.tasks) == 1
    task = progress.tasks[0]

    assert task.completed == _PROGRESS_COMPLETED
    assert task.total == _PROGRESS_TOTAL
    assert "[theme.cat.meta]Execution[/theme.cat.meta]" in task.description


def test_display_status_summary_renders_metrics() -> None:
    summary = status.StatusSummary(
        phase="Review",
        iteration=2,
        total_iterations=4,
        reviewer_pass=1,
        total_reviewer_passes=3,
        metrics={"Success": 7, "Failure": 2},
    )

    console = Console(record=True, theme=RALPH_THEME)
    ctx = make_display_context(console=console)
    status.display_status_summary(summary, display_context=ctx)

    output = console.export_text()

    assert "Phase       │ Review" in output
    assert "Iteration   │ 2/4" in output
    assert "Review Pass │ 1/3" in output
    assert "Success     │ 7" in output
    assert "Failure     │ 2" in output


def test_display_phase_uses_injected_display_context_width() -> None:
    """display_phase uses the console from the injected DisplayContext."""
    stream = StringIO()
    console = Console(
        file=stream, force_terminal=False, color_system=None, width=60, theme=RALPH_THEME
    )
    ctx = make_display_context(env={"COLUMNS": "60"})
    import dataclasses  # noqa: PLC0415

    recording_ctx = dataclasses.replace(ctx, console=console)

    status.display_phase("Planning", iteration=1, total=3, display_context=recording_ctx)

    output = stream.getvalue()
    assert "Phase" in output
    assert "Planning" in output
    assert "Iteration 1 of 3" in output


def test_create_progress_bar_uses_injected_display_context() -> None:
    """create_progress_bar uses the console from the injected DisplayContext."""
    stream = StringIO()
    console = Console(
        file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME
    )
    ctx = make_display_context(env={"COLUMNS": "120"})
    import dataclasses  # noqa: PLC0415

    recording_ctx = dataclasses.replace(ctx, console=console)

    progress = status.create_progress_bar(display_context=recording_ctx)
    assert progress.console is console
