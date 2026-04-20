"""Atomic checkpoint persistence for pipeline resume.

This module handles saving and loading pipeline state checkpoints.
Checkpoints enable the pipeline to resume from interruption without
losing progress.

All writes are atomic (write to .tmp then rename) to prevent
partial checkpoint corruption.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from ralph.pipeline.state import PipelineState

CHECKPOINT_PATH = Path(".agent") / "checkpoint.json"

# Forbidden sentinel strings that indicate a bug in error handling.
# If a pre-fix checkpoint contains one of these as last_error, we drop it
# and log a warning to prevent the old bug from being resurrected.
_FORBIDDEN_ERROR_SENTINELS: frozenset[str] = frozenset(
    {
        "Unknown failure",
        "unknown failure",
        "",
        "None",
        "null",
    }
)


def _cleanup_stray_tmp(path: Path) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.unlink(missing_ok=True)


class Checkpoint:
    def __init__(self, path: Path = CHECKPOINT_PATH) -> None:
        self._path = path
        _cleanup_stray_tmp(path)


def _sanitize_last_error(state: PipelineState) -> PipelineState:
    """Drop forbidden sentinel last_error values loaded from old checkpoints.

    If last_error is None or is a known sentinel string (indicating a bug
    in the old code), replace it with None so the pipeline starts fresh.
    """
    last_error = state.last_error
    if last_error is None:
        return state

    stripped = last_error.strip()
    if stripped == "" or last_error in _FORBIDDEN_ERROR_SENTINELS:
        logger.warning(
            "Dropping stale last_error sentinel {!r} loaded from checkpoint; "
            "pipeline will start with a clean error state.",
            last_error,
        )
        return state.copy_with(last_error=None)
    return state


def save(state: PipelineState, path: Path = CHECKPOINT_PATH) -> None:
    """Atomically write state to disk.

    Writes to a temporary file first, then renames to the target path.
    This ensures no partial checkpoint data on disk if the write is
    interrupted.

    Args:
        state: The pipeline state to save.
        path: Path to save the checkpoint. Defaults to .agent/checkpoint.json.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.debug("Checkpoint saved to {}", path)
    except Exception as exc:
        logger.error("Failed to save checkpoint to {}: {}", path, exc)
        if tmp.exists():
            tmp.unlink()
        raise


def load(path: Path = CHECKPOINT_PATH) -> PipelineState | None:
    """Load checkpoint from disk.

    Args:
        path: Path to the checkpoint file.

    Returns:
        PipelineState if checkpoint exists and is valid, None otherwise.
        If the loaded state contains a forbidden last_error sentinel
        (e.g. "Unknown failure"), it is dropped and replaced with None.
    """
    if not path.exists():
        logger.debug("No checkpoint found at {}", path)
        return None
    try:
        data = path.read_text(encoding="utf-8")
        state = PipelineState.model_validate_json(data)
        # Sanitize any stale error sentinel that might have been saved
        # by a pre-fix version of the pipeline.
        state = _sanitize_last_error(state)
        logger.debug("Checkpoint loaded from {}", path)
        return state
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Corrupt checkpoint at {}: {}", path, exc)
        return None


async def save_async(state: PipelineState, path: Path = CHECKPOINT_PATH) -> None:
    """Atomically write state to disk without blocking the event loop.

    Delegates to :func:`save` via ``asyncio.to_thread`` so callers
    can await this from an async context without stalling the event loop.

    Args:
        state: The pipeline state to save.
        path: Path to save the checkpoint. Defaults to .agent/checkpoint.json.
    """
    await asyncio.to_thread(save, state, path)


async def load_async(path: Path = CHECKPOINT_PATH) -> PipelineState | None:
    """Load checkpoint from disk without blocking the event loop.

    Delegates to :func:`load` via ``asyncio.to_thread`` so callers
    can await this from an async context without stalling the event loop.

    Args:
        path: Path to the checkpoint file.

    Returns:
        PipelineState if checkpoint exists and is valid, None otherwise.
    """
    return await asyncio.to_thread(load, path)


def inspect(path: Path = CHECKPOINT_PATH) -> str:
    """Return formatted checkpoint summary.

    Args:
        path: Path to the checkpoint file.

    Returns:
        Formatted string representation of the checkpoint.
    """
    state = load(path)
    if state is None:
        return "No checkpoint found."
    return state.model_dump_json(indent=2)


def exists(path: Path = CHECKPOINT_PATH) -> bool:
    """Check if a checkpoint exists.

    Args:
        path: Path to the checkpoint file.

    Returns:
        True if checkpoint exists.
    """
    return path.exists()


def remove(path: Path = CHECKPOINT_PATH) -> None:
    """Remove a checkpoint file.

    Args:
        path: Path to the checkpoint file.
    """
    if path.exists():
        path.unlink()
        logger.debug("Checkpoint removed from {}", path)
