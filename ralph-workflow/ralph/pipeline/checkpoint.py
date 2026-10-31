# Deprecated: Ralph-orchestrated workers are removed from the execution model; parallelism is owned by the developer agent's own sub-agents. See docs/sphinx/concepts.md §"Deprecated: Ralph-orchestrated workers".
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

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from ralph.pipeline.state import PipelineState

CHECKPOINT_PATH = Path(".agent") / "checkpoint.json"


def worker_checkpoint_path(worker_namespace: Path) -> Path:
    """Return the worker-local checkpoint path."""
    return worker_namespace / "tmp" / "checkpoint.json"


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


def _staging_path(path: Path) -> Path:
    """Return the staging stem ``save()`` writes through for ``path``.

    The atomic writer derives a unique ``<stem>.<hex>`` file from this stem,
    so both the stem and its suffixed siblings are interrupted-save debris.
    """
    return path.with_suffix(".tmp")


def _cleanup_stray_tmp(
    path: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    staging = _staging_path(path)
    # The legacy ``checkpoint.json.tmp`` spelling is still swept so debris
    # from older releases does not linger beside the canonical file.
    legacy = Path(str(path) + ".tmp")
    unique_prefix = f"{staging.name}."
    stray = [staging, legacy]
    stray.extend(
        candidate
        for candidate in backend.glob(path.parent, f"{unique_prefix}*")
        if candidate.parent == path.parent and candidate.name.startswith(unique_prefix)
    )
    for tmp in stray:
        # filesystem-write-ok: discard interrupted checkpoint staging before recovery reads canonical bytes
        backend.unlink(tmp, missing_ok=True)


class Checkpoint:
    """Handle for atomic checkpoint read/write operations at a fixed path."""

    def __init__(
        self,
        path: Path = CHECKPOINT_PATH,
        *,
        backend: FileBackend = DEFAULT_FILE_BACKEND,
    ) -> None:
        self._path = path
        self._backend = backend
        _cleanup_stray_tmp(path, backend=backend)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def backend(self) -> FileBackend:
        return self._backend

    def save(self, state: PipelineState) -> None:
        save(state, self._path, backend=self._backend)

    def load(self) -> PipelineState | None:
        return load(self._path, backend=self._backend)

    def exists(self) -> bool:
        return exists(self._path, backend=self._backend)

    def remove(self) -> None:
        remove(self._path, backend=self._backend)

    def inspect(self) -> str:
        return inspect(self._path, backend=self._backend)

    async def save_async(self, state: PipelineState) -> None:
        await save_async(state, self._path, backend=self._backend)

    async def load_async(self) -> PipelineState | None:
        return await load_async(self._path, backend=self._backend)


def _normalize_recovery_state(state: PipelineState) -> PipelineState:
    """Normalize recovery lineage to the configured cap before persistence/use."""
    return state.copy_with(
        recovery_cycle_cap=state.recovery_cycle_cap,
        fallover_history=state.fallover_history,
    )


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


def save(
    state: PipelineState,
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Atomically write state to disk.

    Writes to a temporary file first, then renames to the target path.
    This ensures no partial checkpoint data on disk if the write is
    interrupted.

    A byte-identical existing checkpoint short-circuits the temp
    write and the replace so per-iteration saves do not advance
    the file's mtime or generate an additional fseventsd
    notification. The post-condition "checkpoint file contains the
    serialized state" always holds: any read uncertainty or
    content mismatch falls through to a real write.

    Args:
        state: The pipeline state to save.
        path: Path to save the checkpoint. Defaults to .agent/checkpoint.json.
        backend: FileBackend used for the atomic write so the
            idempotent guard can be exercised by an in-memory backend
            under test without real filesystem I/O.
    """
    state = _normalize_recovery_state(state)
    serialized = state.model_dump_json(indent=2)
    # filesystem-write-ok: pipeline checkpoint state under .agent/ or worktree-local path, runtime recovery metadata not tracked
    tmp_path = _staging_path(path)
    try:
        atomic_write_text_if_changed(
            backend,
            path,
            serialized,
            tmp_path=tmp_path,
            sync_directory=True,
            prepare_write=lambda: backend.mkdir(path.parent, parents=True, exist_ok=True),
        )
        logger.debug("Checkpoint saved to {}", path)
    except Exception as exc:
        logger.error("Failed to save checkpoint to {}: {}", path, exc)
        backend.unlink(tmp_path, missing_ok=True)
        raise


def load(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PipelineState | None:
    """Load checkpoint from disk.

    Args:
        path: Path to the checkpoint file.
        backend: FileBackend used for the load operation.

    Returns:
        PipelineState if checkpoint exists and is valid, None otherwise.
        If the loaded state contains a forbidden last_error sentinel
        (e.g. "Unknown failure"), it is dropped and replaced with None.
    """
    if not backend.exists(path):
        logger.debug("No checkpoint found at {}", path)
        return None
    try:
        data = backend.read_text(path, encoding="utf-8")
        parsed: object = json.loads(data)
        if isinstance(parsed, dict) and "state" in parsed and isinstance(parsed["state"], dict):
            # Authoritative state extracted from CheckpointPayload wrapper;
            # projections rebuilt one-way with no newest-timestamp merging.
            state = PipelineState.model_validate(parsed["state"])
        elif isinstance(parsed, dict):
            state = PipelineState.model_validate(parsed)
        else:
            state = PipelineState.model_validate_json(data)
        state = _normalize_recovery_state(state)
        # Sanitize any stale error sentinel that might have been saved
        # by a pre-fix version of the pipeline.
        state = _sanitize_last_error(state)
        logger.debug("Checkpoint loaded from {}", path)
        return state
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("Corrupt checkpoint at {}: {}", path, exc)
        return None


async def save_async(
    state: PipelineState,
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Atomically write state to disk without blocking the event loop.

    Delegates to :func:`save` via ``asyncio.to_thread`` so callers
    can await this from an async context without stalling the event loop.

    Args:
        state: The pipeline state to save.
        path: Path to save the checkpoint. Defaults to .agent/checkpoint.json.
        backend: FileBackend used for the atomic write.
    """
    await asyncio.to_thread(save, state, path, backend=backend)


async def load_async(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PipelineState | None:
    """Load checkpoint from disk without blocking the event loop.

    Delegates to :func:`load` via ``asyncio.to_thread`` so callers
    can await this from an async context without stalling the event loop.

    Args:
        path: Path to the checkpoint file.
        backend: FileBackend used for the load operation.

    Returns:
        PipelineState if checkpoint exists and is valid, None otherwise.
    """
    return await asyncio.to_thread(load, path, backend=backend)


def inspect(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str:
    """Return formatted checkpoint summary.

    Args:
        path: Path to the checkpoint file.
        backend: FileBackend used for reading.

    Returns:
        Formatted string representation of the checkpoint.
    """
    state = load(path, backend=backend)
    if state is None:
        return "No checkpoint found."
    return state.model_dump_json(indent=2)


def exists(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    """Check if a checkpoint exists.

    Args:
        path: Path to the checkpoint file.
        backend: FileBackend used to check existence.

    Returns:
        True if checkpoint exists.
    """
    return backend.exists(path)


def remove(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Remove a checkpoint file.

    Args:
        path: Path to the checkpoint file.
        backend: FileBackend used for unlinking.
    """
    if backend.exists(path):
        backend.unlink(path)
        logger.debug("Checkpoint removed from {}", path)
