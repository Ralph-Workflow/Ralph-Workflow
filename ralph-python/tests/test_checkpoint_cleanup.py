"""Tests for stray .tmp file cleanup on Checkpoint init."""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.checkpoint import Checkpoint
from ralph.pipeline.state import PipelineState

THIRD_ITERATION = 3


def test_stray_tmp_removed_on_init(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    tmp = Path(str(path) + ".tmp")
    tmp.write_text("stray content", encoding="utf-8")

    Checkpoint(path)

    assert not tmp.exists()


def test_no_tmp_file_safe_on_init(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"

    # Should not raise
    Checkpoint(path)


def test_actual_checkpoint_preserved(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    state = PipelineState(iteration=THIRD_ITERATION)
    ckpt.save(state, path)

    tmp = Path(str(path) + ".tmp")
    tmp.write_text("stray", encoding="utf-8")

    Checkpoint(path)

    assert path.exists()
    loaded = ckpt.load(path)
    assert loaded is not None
    assert loaded.iteration == THIRD_ITERATION
    assert not tmp.exists()
