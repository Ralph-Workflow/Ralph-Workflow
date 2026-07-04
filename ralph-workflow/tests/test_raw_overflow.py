"""Unit tests for the RawOverflowLog class."""

from __future__ import annotations

import threading
from pathlib import Path

from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.display.raw_overflow import RawOverflowLog


def test_append_writes_lines(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line one")
    log.append("line two")
    log.flush()
    content = log.path.read_text(encoding="utf-8")
    assert "line one\n" in content
    assert "line two\n" in content
    log.close()


def test_first_write_truncates_previous_content(tmp_path: Path) -> None:
    log1 = RawOverflowLog(tmp_path, "unit-1")
    log1.append("run1 line")
    log1.close()

    log2 = RawOverflowLog(tmp_path, "unit-1")
    log2.append("run2 line")
    log2.flush()

    content = log2.path.read_text(encoding="utf-8")
    assert "run1 line" not in content
    assert "run2 line" in content
    log2.close()


def test_unit_id_sanitization(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit/with:special chars!")
    log.append("test")
    log.flush()
    assert log.path.name == "unit_with_special_chars_.log"
    assert log.path.exists()
    log.close()


def test_relative_reference(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    ref = log.relative_reference(tmp_path)
    assert ref == ".agent/raw/unit-1.log"


def test_relative_reference_absolute_fallback(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    other_root = Path("/some/other/path")
    ref = log.relative_reference(other_root)
    assert ref == log.path.as_posix()


def test_silent_noop_when_parent_is_a_file(tmp_path: Path) -> None:
    # Create a file where the .agent/raw directory should be
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    raw_file = agent_dir / "raw"
    raw_file.write_text("not a directory", encoding="utf-8")

    log = RawOverflowLog(tmp_path, "unit-1")
    # Should not raise even though the path is a file, not a directory
    log.append("test line")
    # Black-box check: the per-unit log file should not exist as a regular file
    # since mkdir failed; the append silently no-oped.
    assert not log.path.is_file()


def test_thread_safety(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    errors: list[Exception] = []

    def write_lines() -> None:
        try:
            for i in range(20):
                log.append(f"line {i}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_lines) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    log.close()


def test_append_strips_trailing_newline(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line with newline\n")
    log.flush()
    content = log.path.read_text(encoding="utf-8")
    assert content == "line with newline\n"
    assert not content.endswith("\n\n")
    log.close()


def test_append_hard_stops_at_max_bytes(tmp_path: Path) -> None:
    max_bytes = 16
    log = RawOverflowLog(tmp_path, "unit-1", max_bytes=max_bytes)

    assert log.append("1234567") is True  # 8 bytes with trailing newline
    assert log.append("abcdefg") is True  # 8 bytes with trailing newline
    assert log.append("overflow") is False

    log.flush()
    assert log.path.stat().st_size == max_bytes
    assert log.path.read_text(encoding="utf-8") == "1234567\nabcdefg\n"


def test_size_bytes_returns_zero_before_first_write(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    assert not log.path.exists()
    assert log.size_bytes == 0


def test_size_bytes_uses_fast_path_after_first_write(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line one")
    expected = len(b"line one\n")
    assert expected == log._bytes_written
    assert log.size_bytes == expected
    assert log.size_bytes == log._bytes_written
    log.close()


def test_size_bytes_returns_bytes_written_when_disabled(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("first line")
    log.disable()
    assert log.size_bytes == log._bytes_written


def test_size_bytes_returns_zero_when_file_missing_after_write(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("some content")
    log.flush()
    assert log.path.exists()
    log.path.unlink()
    assert not log.path.exists()
    assert log.size_bytes == 0


def test_size_bytes_returns_zero_when_prior_run_file_exists(tmp_path: Path) -> None:
    log1 = RawOverflowLog(tmp_path, "unit-1")
    log1.append("prior run content")
    log1.flush()
    prior_size = log1.path.stat().st_size
    assert prior_size > 0

    log2 = RawOverflowLog(tmp_path, "unit-1")
    assert log2.size_bytes == 0

    log2.append("current run content")
    assert log2.size_bytes == len(b"current run content\n")


def test_is_disabled_true_after_max_bytes(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1", max_bytes=16)
    assert log.is_disabled is False

    log.append("1234567")
    assert log.is_disabled is False

    log.append("abcdefg")
    assert log.is_disabled is False

    log.append("overflow attempt")
    assert log.is_disabled is True


def test_is_disabled_true_after_io_error(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    raw_dir = agent_dir / "raw"
    raw_dir.mkdir()

    raw_file = raw_dir / "unit-1.log"
    raw_file.write_text("content", encoding="utf-8")
    raw_file.chmod(0o000)

    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("new content")
    assert log.is_disabled is True


# New tests for buffered handle, time-based flush, and explicit close().


def test_append_keeps_handle_open_and_buffers(tmp_path: Path) -> None:
    """Writes are buffered; flush() makes them visible on disk."""
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=3600.0)
    log.append("buffered line")
    # size_bytes must track appends immediately (watchdog liveness contract)
    assert log.size_bytes == len(b"buffered line\n")
    log.flush()
    assert "buffered line\n" in log.path.read_text(encoding="utf-8")
    log.close()


def test_time_based_flush(tmp_path: Path) -> None:
    fake_time = [0.0]
    log = RawOverflowLog(
        tmp_path, "unit-1", flush_interval_seconds=5.0, now=lambda: fake_time[0]
    )
    log.append("first")
    fake_time[0] = 6.0
    log.append("second")  # crosses the interval -> flush
    log.close()
    content = log.path.read_text(encoding="utf-8")
    assert "first\n" in content
    assert "second\n" in content


def test_close_flushes_and_reopen_appends(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=3600.0)
    log.append("before close")
    log.close()
    assert "before close\n" in log.path.read_text(encoding="utf-8")
    log.append("after close")  # reopens in append mode
    log.close()
    content = log.path.read_text(encoding="utf-8")
    assert "before close\n" in content
    assert "after close\n" in content


def test_close_is_idempotent(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("x")
    log.close()
    log.close()  # no raise


def test_executor_drop_unit_closes_raw_log(tmp_path: Path) -> None:
    """SubprocessAgentExecutor.drop_unit() must close the raw log and
    flush its buffered tail to disk (RFC-013 P1)."""
    executor = SubprocessAgentExecutor.__new__(SubprocessAgentExecutor)
    executor._raw_logs = {}
    executor._raw_overflow_root = tmp_path
    executor._cwd = tmp_path
    log = executor._get_raw_log("unit-x")
    log.append("pending line")
    executor.drop_unit("unit-x")
    # close() during drop must have flushed the buffered tail
    assert "pending line\n" in (
        tmp_path / ".agent" / "raw" / "unit-x.log"
    ).read_text(encoding="utf-8")
