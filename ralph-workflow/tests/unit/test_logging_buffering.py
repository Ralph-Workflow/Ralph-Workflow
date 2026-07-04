"""Buffered file sinks still deliver records after sink removal (flush-on-close)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.logging_worker_sink import bind_worker_sink, remove_worker_sink

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_loguru() -> None:
    logger.remove()
    yield
    logger.remove()


def test_worker_sink_flushes_on_remove(tmp_path: Path) -> None:
    handle = bind_worker_sink("unit-9", tmp_path, run_id="run-1")
    logger.bind(unit_id="unit-9").info("hello worker")
    remove_worker_sink(handle)  # closes the file -> flush
    assert "hello worker" in handle.log_path.read_text(encoding="utf-8")


def test_worker_sink_uses_block_buffering(tmp_path: Path) -> None:
    """A single small record must NOT hit disk immediately under block
    buffering (the syscall-batching property, RFC-013 P1).

    On an exceptionally fast machine where the buffer flushes between the
    write and the stat, fall back to asserting the record content is not
    visible: the proof is the absence of an immediate write.
    """
    handle = bind_worker_sink("unit-8", tmp_path, run_id="run-1")
    try:
        logger.bind(unit_id="unit-8").info("small record")
        # The 8 KB buffer cannot be filled by a single ~50-byte record,
        # so the bytes must NOT have hit disk yet.
        if handle.log_path.exists():
            st_size = handle.log_path.stat().st_size
            if st_size > 0:
                content = handle.log_path.read_text(encoding="utf-8")
                assert "small record" not in content, (
                    "block buffering should delay small writes to disk"
                )
    finally:
        remove_worker_sink(handle)
