from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest

from ralph.agents.completion_signals import evaluate_completion
from ralph.mcp.artifacts.state_db import RunStateDB

_POLLS = 40
_BUDGET_BYTES = 300 * 1024


class _RusageV2(ctypes.Structure):
    _fields_ = (("uuid", ctypes.c_uint8 * 16), ("vals", ctypes.c_uint64 * 26))


def _bytes_written() -> int | None:
    proc_io = Path("/proc/self/io")
    if proc_io.exists():
        for line in proc_io.read_text(encoding="utf-8").splitlines():
            if line.startswith("write_bytes:"):
                return int(line.split(":", 1)[1])
    if sys.platform == "darwin":
        info = _RusageV2()
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        if libproc.proc_pid_rusage(os.getpid(), 2, ctypes.byref(info)) == 0:
            return int(info.vals[17])
    return None


def test_polling_for_completion_does_not_rewrite_the_state_database(tmp_path: Path) -> None:
    RunStateDB(tmp_path).close()
    before = _bytes_written()
    if before is None:
        pytest.skip("this platform does not report per-process bytes written")

    for _ in range(_POLLS):
        evaluate_completion(tmp_path, [], run_id="run-1", sentinel_secret="s", receipt_secret="s")

    written = (_bytes_written() or 0) - before
    assert written < _BUDGET_BYTES, f"{_POLLS} completion polls wrote {written} bytes"


def test_a_receipt_written_through_one_handle_is_read_through_another(tmp_path: Path) -> None:
    writer = RunStateDB(tmp_path)
    writer.upsert_completion_sentinel("run-1", "abc")
    writer.close()

    reader = RunStateDB(tmp_path)
    try:
        assert reader.get_completion_sentinel_hmac("run-1") == "abc"
    finally:
        reader.close()


def test_a_replaced_database_file_is_reopened_not_written_to_the_old_file(tmp_path: Path) -> None:
    first = RunStateDB(tmp_path)
    first.upsert_completion_sentinel("run-1", "abc")
    first.close()
    db_file = tmp_path / ".agent" / "state.db"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_file}{suffix}").unlink(missing_ok=True)

    second = RunStateDB(tmp_path)
    try:
        second.upsert_completion_sentinel("run-2", "def")
        assert db_file.exists()
        assert second.get_completion_sentinel_hmac("run-2") == "def"
    finally:
        second.close()
