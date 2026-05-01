"""Async tests for checkpoint persistence.

Tests save_async and load_async wrappers that delegate to asyncio.to_thread()
so they do not block the event loop.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from pathlib import Path

DEVELOPMENT_ITERATION = 2
TOTAL_ITERATIONS = 5


def _build_state() -> PipelineState:
    return PipelineState(
        phase="development",
        iteration=DEVELOPMENT_ITERATION,
        total_iterations=TOTAL_ITERATIONS,
    )


async def test_save_async_creates_file(tmp_path: Path) -> None:
    """save_async writes state to disk, same as sync save."""
    state = _build_state()
    path = tmp_path / "checkpoint.json"

    await ckpt.save_async(state, path)

    assert path.exists()


async def test_save_async_roundtrip(tmp_path: Path) -> None:
    """State saved with save_async can be loaded with load."""
    state = _build_state()
    path = tmp_path / "checkpoint.json"

    await ckpt.save_async(state, path)
    loaded = ckpt.load(path)

    assert loaded is not None
    assert loaded.phase == "development"
    assert loaded.iteration == DEVELOPMENT_ITERATION
    assert loaded.total_iterations == TOTAL_ITERATIONS


async def test_load_async_returns_state(tmp_path: Path) -> None:
    """load_async reads a checkpoint written by sync save."""
    state = _build_state()
    path = tmp_path / "checkpoint.json"

    ckpt.save(state, path)
    loaded = await ckpt.load_async(path)

    assert loaded is not None
    assert loaded.phase == "development"
    assert loaded.iteration == DEVELOPMENT_ITERATION


async def test_load_async_returns_none_when_missing(tmp_path: Path) -> None:
    """load_async returns None when no checkpoint exists."""
    path = tmp_path / "nonexistent.json"
    result = await ckpt.load_async(path)
    assert result is None


async def test_save_async_and_load_async_roundtrip(tmp_path: Path) -> None:
    """save_async + load_async round-trip preserves full state."""
    state = _build_state()
    path = tmp_path / "checkpoint.json"

    await ckpt.save_async(state, path)
    loaded = await ckpt.load_async(path)

    assert loaded is not None
    assert loaded.phase == state.phase
    assert loaded.iteration == state.iteration
    assert loaded.total_iterations == state.total_iterations


async def test_save_async_nonblocking(tmp_path: Path) -> None:
    concurrent_ran = asyncio.Event()

    async def set_event() -> None:
        concurrent_ran.set()

    state = _build_state()
    path = tmp_path / "checkpoint.json"

    await asyncio.gather(
        ckpt.save_async(state, path),
        set_event(),
    )

    assert concurrent_ran.is_set(), "Event loop was blocked — concurrent coroutine never ran"


async def test_load_async_nonblocking(tmp_path: Path) -> None:
    concurrent_ran = asyncio.Event()

    async def set_event() -> None:
        concurrent_ran.set()

    state = _build_state()
    path = tmp_path / "checkpoint.json"
    ckpt.save(state, path)

    await asyncio.gather(
        ckpt.load_async(path),
        set_event(),
    )

    assert concurrent_ran.is_set(), "Event loop was blocked — concurrent coroutine never ran"
