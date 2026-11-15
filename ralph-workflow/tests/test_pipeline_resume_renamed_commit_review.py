from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.checkpoint import Checkpoint, CheckpointRetiredPhaseError
from ralph.pipeline.state import PipelineState

_RETIRED_PHASE = "development_commit_" + "cleanup"
_RETIRED_FINAL_PHASE = "development_final_commit_" + "cleanup"
_RETIRED_ARTIFACT = "commit_" + "cleanup"
_REPLACEMENT_ARTIFACT = "commit_review"


def test_load_checkpoint_with_retired_commit_review_phase_raises(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint_data = {
        "phase": _RETIRED_PHASE,
        "outer_progress": {"iteration": 1},
        "budget_caps": {"iteration": 5},
    }
    path.write_text(json.dumps(checkpoint_data), encoding="utf-8")

    with pytest.raises(CheckpointRetiredPhaseError) as exc_info:
        ckpt.load(path)

    message = str(exc_info.value)
    assert _RETIRED_PHASE in message
    assert _RETIRED_ARTIFACT in message
    assert _REPLACEMENT_ARTIFACT in message


def test_load_checkpoint_with_retired_final_commit_review_phase_raises(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint_data = {
        "phase": _RETIRED_FINAL_PHASE,
        "outer_progress": {"iteration": 1},
        "budget_caps": {"iteration": 5},
    }
    path.write_text(json.dumps(checkpoint_data), encoding="utf-8")

    with pytest.raises(CheckpointRetiredPhaseError) as exc_info:
        ckpt.load(path)

    message = str(exc_info.value)
    assert _RETIRED_FINAL_PHASE in message
    assert _RETIRED_ARTIFACT in message
    assert _REPLACEMENT_ARTIFACT in message


def test_load_checkpoint_with_retired_policy_entry_phase_raises(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint_data = {
        "phase": "planning",
        "policy_entry_phase": _RETIRED_PHASE,
    }
    path.write_text(json.dumps(checkpoint_data), encoding="utf-8")

    with pytest.raises(CheckpointRetiredPhaseError) as exc_info:
        ckpt.load(path)

    message = str(exc_info.value)
    assert _RETIRED_PHASE in message
    assert _RETIRED_ARTIFACT in message
    assert _REPLACEMENT_ARTIFACT in message


def test_load_checkpoint_with_retired_phase_chains_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint_data = {
        "phase": "planning",
        "phase_chains": {
            _RETIRED_PHASE: {
                "agents": ["claude"],
                "current_index": 0,
            }
        },
    }
    path.write_text(json.dumps(checkpoint_data), encoding="utf-8")

    with pytest.raises(CheckpointRetiredPhaseError) as exc_info:
        ckpt.load(path)

    message = str(exc_info.value)
    assert _RETIRED_PHASE in message
    assert _RETIRED_ARTIFACT in message
    assert _REPLACEMENT_ARTIFACT in message


async def test_load_async_checkpoint_with_retired_phase_raises(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint_data = {
        "phase": _RETIRED_PHASE,
    }
    path.write_text(json.dumps(checkpoint_data), encoding="utf-8")

    with pytest.raises(CheckpointRetiredPhaseError) as exc_info:
        await ckpt.load_async(path)

    message = str(exc_info.value)
    assert _RETIRED_PHASE in message
    assert _RETIRED_ARTIFACT in message
    assert _REPLACEMENT_ARTIFACT in message


def test_checkpoint_instance_load_with_retired_phase_raises(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint_data = {
        "phase": _RETIRED_PHASE,
    }
    path.write_text(json.dumps(checkpoint_data), encoding="utf-8")

    checkpoint = Checkpoint(path=path)
    with pytest.raises(CheckpointRetiredPhaseError) as exc_info:
        checkpoint.load()

    message = str(exc_info.value)
    assert _RETIRED_PHASE in message
    assert _RETIRED_ARTIFACT in message
    assert _REPLACEMENT_ARTIFACT in message


def test_pipeline_state_validation_with_retired_phase_raises() -> None:
    with pytest.raises(CheckpointRetiredPhaseError) as exc_info:
        PipelineState.model_validate({"phase": _RETIRED_PHASE})

    message = str(exc_info.value)
    assert _RETIRED_PHASE in message
    assert _RETIRED_ARTIFACT in message
    assert _REPLACEMENT_ARTIFACT in message
