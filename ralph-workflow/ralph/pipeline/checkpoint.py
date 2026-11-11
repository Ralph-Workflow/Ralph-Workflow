
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
    return worker_namespace / "tmp" / "checkpoint.json"


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
    return path.with_suffix(".tmp")


def _cleanup_stray_tmp(
    path: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    staging = _staging_path(path)
    legacy = Path(str(path) + ".tmp")
    unique_prefix = f"{staging.name}."
    stray = [staging, legacy]
    stray.extend(
        candidate
        for candidate in backend.glob(path.parent, f"{unique_prefix}*")
        if candidate.parent == path.parent and candidate.name.startswith(unique_prefix)
    )
    for tmp in stray:
        backend.unlink(tmp, missing_ok=True)


class Checkpoint:

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
    return state.copy_with(
        recovery_cycle_cap=state.recovery_cycle_cap,
        fallover_history=state.fallover_history,
    )


def _sanitize_last_error(state: PipelineState) -> PipelineState:
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
    state = _normalize_recovery_state(state)
    serialized = state.model_dump_json(indent=2)
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
    if not backend.exists(path):
        logger.debug("No checkpoint found at {}", path)
        return None
    try:
        data = backend.read_text(path, encoding="utf-8")
        parsed: object = json.loads(data)
        if isinstance(parsed, dict) and "state" in parsed and isinstance(parsed["state"], dict):
            state = PipelineState.model_validate(parsed["state"])
        elif isinstance(parsed, dict):
            state = PipelineState.model_validate(parsed)
        else:
            state = PipelineState.model_validate_json(data)
        state = _normalize_recovery_state(state)
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
    await asyncio.to_thread(save, state, path, backend=backend)


async def load_async(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PipelineState | None:
    return await asyncio.to_thread(load, path, backend=backend)


def inspect(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str:
    state = load(path, backend=backend)
    if state is None:
        return "No checkpoint found."
    return state.model_dump_json(indent=2)


def exists(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    return backend.exists(path)


def remove(
    path: Path = CHECKPOINT_PATH,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    if backend.exists(path):
        backend.unlink(path)
        logger.debug("Checkpoint removed from {}", path)
