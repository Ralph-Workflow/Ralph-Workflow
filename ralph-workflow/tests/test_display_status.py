from rich.console import Console

from ralph.display import status

_PROGRESS_COMPLETED = 2
_PROGRESS_TOTAL = 5


def test_display_phase_shows_phase_and_iteration() -> None:
    console = Console(record=True)
    status.display_phase("Planning", iteration=1, total=3, console=console)

    output = console.export_text()

    assert "Phase: Planning" in output
    assert "Iteration 1 of 3" in output


def test_display_progress_sets_task_state() -> None:
    progress = status.display_progress(
        current=_PROGRESS_COMPLETED,
        total=_PROGRESS_TOTAL,
        phase="Execution",
    )

    assert len(progress.tasks) == 1
    task = progress.tasks[0]

    assert task.completed == _PROGRESS_COMPLETED
    assert task.total == _PROGRESS_TOTAL
    assert "[cyan]Execution[/cyan]" in task.description


def test_display_status_summary_renders_metrics() -> None:
    summary = status.StatusSummary(
        phase="Review",
        iteration=2,
        total_iterations=4,
        reviewer_pass=1,
        total_reviewer_passes=3,
        metrics={"Success": 7, "Failure": 2},
    )

    console = Console(record=True)
    status.display_status_summary(summary, console=console)

    output = console.export_text()

    assert "Phase       │ Review" in output
    assert "Iteration   │ 2/4" in output
    assert "Review Pass │ 1/3" in output
    assert "Success     │ 7" in output
    assert "Failure     │ 2" in output
