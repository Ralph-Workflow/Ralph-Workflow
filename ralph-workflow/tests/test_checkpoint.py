"""Unit tests for checkpoint persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_REVIEW
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import (
    AgentChainState,
    CommitState,
    PipelineState,
    RebaseState,
    RunMetrics,
)

DEVELOPMENT_ITERATION = 2
TOTAL_ITERATIONS = 5
TOTAL_AGENT_CALLS = 10


def test_save_and_load_checkpoint(tmp_path: Path) -> None:
    """Test saving and loading a checkpoint."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        iteration=DEVELOPMENT_ITERATION,
        total_iterations=TOTAL_ITERATIONS,
    )
    path = tmp_path / "checkpoint.json"

    ckpt.save(state, path)
    assert path.exists()

    loaded = ckpt.load(path)
    assert loaded is not None
    assert loaded.phase == PHASE_DEVELOPMENT
    assert loaded.iteration == DEVELOPMENT_ITERATION
    assert loaded.total_iterations == TOTAL_ITERATIONS


def test_load_nonexistent_checkpoint(tmp_path: Path) -> None:
    """Test loading a checkpoint that doesn't exist."""
    path = tmp_path / "nonexistent.json"
    result = ckpt.load(path)
    assert result is None


def test_checkpoint_exists(tmp_path: Path) -> None:
    """Test checking if checkpoint exists."""
    path = tmp_path / "checkpoint.json"
    assert ckpt.exists(path) is False

    state = PipelineState()
    ckpt.save(state, path)
    assert ckpt.exists(path) is True


def test_checkpoint_inspect(tmp_path: Path) -> None:
    """Test checkpoint inspection."""
    state = PipelineState(
        phase=PHASE_REVIEW,
        iteration=1,
        total_iterations=3,
        reviewer_pass=0,
        total_reviewer_passes=2,
    )
    path = tmp_path / "checkpoint.json"
    ckpt.save(state, path)

    summary = ckpt.inspect(path)
    assert "review" in summary.lower()


def test_checkpoint_remove(tmp_path: Path) -> None:
    """Test removing a checkpoint."""
    path = tmp_path / "checkpoint.json"
    state = PipelineState()
    ckpt.save(state, path)
    assert path.exists()

    ckpt.remove(path)
    assert not path.exists()


def test_checkpoint_roundtrip_full_state(tmp_path: Path) -> None:
    """Test saving and loading full state with all fields."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        iteration=3,
        total_iterations=TOTAL_ITERATIONS,
        reviewer_pass=1,
        total_reviewer_passes=2,
        review_issues_found=True,
        dev_chain=AgentChainState(agents=["claude", "opencode"], current_index=1),
        rev_chain=AgentChainState(agents=["claude"], current_index=0),
        rebase=RebaseState(pending=True),
        commit=CommitState(message_prepared=True),
        metrics=RunMetrics(total_agent_calls=TOTAL_AGENT_CALLS, total_continuations=2),
        checkpoint_saved_count=TOTAL_ITERATIONS,
        recovery_epoch=1,
        git_auth_configured=True,
    )
    path = tmp_path / "checkpoint.json"

    ckpt.save(state, path)
    loaded = ckpt.load(path)

    assert loaded is not None
    assert loaded.phase == state.phase
    assert loaded.iteration == state.iteration
    assert loaded.dev_chain.current_index == 1
    assert loaded.metrics.total_agent_calls == TOTAL_AGENT_CALLS
    assert loaded.git_auth_configured is True


def test_checkpoint_roundtrip_preserves_current_drain() -> None:
    """Resume checkpoints must keep the exact drain identity."""

    state = PipelineState(current_drain="development_analysis")
    restored = PipelineState.model_validate_json(state.model_dump_json())

    assert restored.current_drain == "development_analysis"


def test_save_failure_removes_tmp_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ensure failed saves clean up the temporary file."""

    path = tmp_path / "checkpoint.json"
    tmp = path.with_suffix(".tmp")
    state = PipelineState()

    original_replace = Path.replace

    def raise_on_tmp(self: Path, target: Path) -> Path:
        if self == tmp:
            raise RuntimeError("disk busy")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", raise_on_tmp)

    with pytest.raises(RuntimeError):
        ckpt.save(state, path)

    assert not tmp.exists()


def test_load_corrupt_checkpoint_returns_none(tmp_path: Path) -> None:
    """Loading invalid JSON should return None."""

    path = tmp_path / "checkpoint.json"
    path.write_text("not a json", encoding="utf-8")

    assert ckpt.load(path) is None


def test_load_invalid_model_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Model validation errors should be treated as corrupt checkpoints."""

    path = tmp_path / "checkpoint.json"
    path.write_text("{}", encoding="utf-8")

    def fake_validate(*args: object, **kwargs: object) -> PipelineState:
        raise ValueError("oops")

    monkeypatch.setattr(ckpt.PipelineState, "model_validate_json", classmethod(fake_validate))

    assert ckpt.load(path) is None


def test_inspect_no_checkpoint_reports_missing(tmp_path: Path) -> None:
    """Inspecting a missing checkpoint returns the friendly message."""

    path = tmp_path / "checkpoint.json"
    assert ckpt.inspect(path) == "No checkpoint found."


def test_remove_nonexistent_checkpoint_idempotent(tmp_path: Path) -> None:
    """Removing a missing checkpoint is a no-op."""

    path = tmp_path / "checkpoint.json"
    ckpt.remove(path)


def test_load_drops_unknown_failure_sentinel(tmp_path: Path) -> None:
    """Checkpoints with 'Unknown failure' last_error should be sanitized on load.

    Pre-fix checkpoints may contain the 'Unknown failure' sentinel in last_error.
    Loading such a checkpoint must drop the sentinel and replace it with None
    so the old bug cannot be resurrected after an upgrade.
    """
    path = tmp_path / "checkpoint.json"

    # Create a checkpoint with the forbidden sentinel
    state_with_sentinel = PipelineState(
        phase=PHASE_DEVELOPMENT,
        iteration=1,
        last_error="Unknown failure",
    )
    ckpt.save(state_with_sentinel, path)

    # Load it
    loaded = ckpt.load(path)
    assert loaded is not None

    # The sentinel must be dropped
    assert loaded.last_error is None, (
        f"Expected last_error to be None after sanitization, got: {loaded.last_error!r}"
    )


def test_load_drops_empty_string_sentinel(tmp_path: Path) -> None:
    """Checkpoints with empty-string last_error should be sanitized on load."""
    path = tmp_path / "checkpoint.json"

    state_with_empty = PipelineState(
        phase=PHASE_DEVELOPMENT,
        iteration=1,
        last_error="",
    )
    ckpt.save(state_with_empty, path)

    loaded = ckpt.load(path)
    assert loaded is not None
    assert loaded.last_error is None


def test_load_drops_none_sentinel(tmp_path: Path) -> None:
    """Checkpoints with 'None' string last_error should be sanitized on load."""
    path = tmp_path / "checkpoint.json"

    state_with_none = PipelineState(
        phase=PHASE_DEVELOPMENT,
        iteration=1,
        last_error="None",
    )
    ckpt.save(state_with_none, path)

    loaded = ckpt.load(path)
    assert loaded is not None
    assert loaded.last_error is None


def test_load_preserves_valid_last_error(tmp_path: Path) -> None:
    """Checkpoints with valid last_error should be preserved."""
    path = tmp_path / "checkpoint.json"

    valid_error = "development: Missing planning artifact at .agent/artifacts/plan.json"
    state_with_valid = PipelineState(
        phase=PHASE_DEVELOPMENT,
        iteration=1,
        last_error=valid_error,
    )
    ckpt.save(state_with_valid, path)

    loaded = ckpt.load(path)
    assert loaded is not None
    assert loaded.last_error == valid_error
