"""Unit tests for checkpoint persistence."""

from __future__ import annotations

from pathlib import Path

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_REVIEW
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import PipelineState


def test_save_and_load_checkpoint(tmp_path: Path) -> None:
    """Test saving and loading a checkpoint."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        iteration=2,
        total_iterations=5,
    )
    path = tmp_path / "checkpoint.json"

    ckpt.save(state, path)
    assert path.exists()

    loaded = ckpt.load(path)
    assert loaded is not None
    assert loaded.phase == PHASE_DEVELOPMENT
    assert loaded.iteration == 2
    assert loaded.total_iterations == 5


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
    from ralph.pipeline.state import AgentChainState, CommitState, RebaseState, RunMetrics

    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        iteration=3,
        total_iterations=5,
        reviewer_pass=1,
        total_reviewer_passes=2,
        review_issues_found=True,
        dev_chain=AgentChainState(agents=["claude", "opencode"], current_index=1),
        rev_chain=AgentChainState(agents=["claude"], current_index=0),
        rebase=RebaseState(pending=True),
        commit=CommitState(message_prepared=True),
        metrics=RunMetrics(total_agent_calls=10, total_continuations=2),
        checkpoint_saved_count=5,
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
    assert loaded.metrics.total_agent_calls == 10
    assert loaded.git_auth_configured is True


def test_checkpoint_roundtrip_preserves_current_drain() -> None:
    """Resume checkpoints must keep the exact drain identity."""

    state = PipelineState(current_drain="development_analysis")
    restored = PipelineState.model_validate_json(state.model_dump_json())

    assert restored.current_drain == "development_analysis"
