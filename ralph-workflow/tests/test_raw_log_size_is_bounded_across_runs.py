from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

from ralph.display.raw_overflow import (
    RawOverflowLog,
    close_all_raw_overflow_logs,
    reset_raw_overflow_path_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_CAP = 4096
_LINE = "x" * 99


@pytest.fixture(autouse=True)
def _fresh_process_state() -> Iterator[None]:
    reset_raw_overflow_path_state()
    yield
    close_all_raw_overflow_logs()
    reset_raw_overflow_path_state()


def _log(root: Path) -> RawOverflowLog:
    return RawOverflowLog(root, "pi", model="minimax/MiniMax-M3", max_bytes=_CAP)


def _seed(root: Path, size: int, *, age_seconds: float) -> Path:
    path = _log(root).path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"y" * size)
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    reset_raw_overflow_path_state()
    return path


def test_a_log_left_by_an_earlier_run_does_not_keep_growing(tmp_path: Path) -> None:
    path = _seed(tmp_path, _CAP * 3, age_seconds=3600)

    log = _log(tmp_path)
    for _ in range(5):
        log.append(_LINE)
    log.disable()

    assert path.stat().st_size <= _CAP


def test_the_earlier_run_is_kept_once_as_the_previous_log(tmp_path: Path) -> None:
    path = _seed(tmp_path, _CAP * 3, age_seconds=3600)

    log = _log(tmp_path)
    log.append(_LINE)
    log.disable()

    previous = path.with_name(path.stem + ".previous.log")
    assert previous.stat().st_size == _CAP * 3


def test_appending_to_a_live_log_counts_the_bytes_already_there(tmp_path: Path) -> None:
    path = _seed(tmp_path, _CAP - 50, age_seconds=-60)

    log = _log(tmp_path)
    for _ in range(5):
        log.append(_LINE)
    log.disable()

    assert path.stat().st_size <= _CAP
