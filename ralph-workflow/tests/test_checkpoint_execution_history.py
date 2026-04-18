"""Unit tests for checkpoint execution history helpers."""

from __future__ import annotations

from importlib import import_module

checkpoint_module = import_module("ralph.checkpoint")
ExecutionHistory = checkpoint_module.ExecutionHistory
ExecutionStep = checkpoint_module.ExecutionStep
StepOutcome = checkpoint_module.StepOutcome


def test_execution_step_success_defaults_exit_code_and_files() -> None:
    """Success outcomes should carry sensible defaults."""
    step = ExecutionStep.new(
        phase="development",
        iteration=1,
        step_type="agent_run",
        outcome=StepOutcome.success(output="done", files_modified=["a.py"]),
    )

    assert step.phase == "development"
    assert step.outcome.kind == "success"
    assert step.outcome.exit_code == 0
    assert step.outcome.files_modified == ["a.py"]


def test_execution_history_add_step_bounded_keeps_recent_tail() -> None:
    """Bounded history should drop older steps when over limit."""
    history = ExecutionHistory.new()

    history = history.add_step_bounded(
        ExecutionStep.new("development", 1, "agent_run", StepOutcome.success()),
        limit=2,
    )
    history = history.add_step_bounded(
        ExecutionStep.new("development", 2, "agent_run", StepOutcome.success()),
        limit=2,
    )
    history = history.add_step_bounded(
        ExecutionStep.new("development", 3, "agent_run", StepOutcome.success()),
        limit=2,
    )

    assert [step.iteration for step in history.steps] == [2, 3]


def test_execution_history_clone_bounded_preserves_tail_only() -> None:
    """clone_bounded should preserve file snapshots while trimming steps."""
    history = ExecutionHistory.new(file_snapshots={"PROMPT.md": "abc123"})
    for iteration in range(1, 5):
        history = history.add_step_bounded(
            ExecutionStep.new("development", iteration, "agent_run", StepOutcome.success()),
            limit=10,
        )

    cloned = history.clone_bounded(limit=2)

    assert [step.iteration for step in cloned.steps] == [3, 4]
    assert cloned.file_snapshots == {"PROMPT.md": "abc123"}
