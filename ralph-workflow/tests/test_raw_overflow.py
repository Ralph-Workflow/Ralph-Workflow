"""Unit tests for the RawOverflowLog class."""

from __future__ import annotations

import threading
from pathlib import Path

from ralph.display.raw_overflow import RawOverflowLog


def test_append_writes_lines(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line one")
    log.append("line two")
    content = log.path.read_text(encoding="utf-8")
    assert "line one\n" in content
    assert "line two\n" in content


def test_first_write_truncates_previous_content(tmp_path: Path) -> None:
    log1 = RawOverflowLog(tmp_path, "unit-1")
    log1.append("run1 line")

    log2 = RawOverflowLog(tmp_path, "unit-1")
    log2.append("run2 line")

    content = log2.path.read_text(encoding="utf-8")
    assert "run1 line" not in content
    assert "run2 line" in content


def test_unit_id_sanitization(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit/with:special chars!")
    log.append("test")
    assert log.path.name == "unit_with_special_chars_.log"
    assert log.path.exists()


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


def test_append_strips_trailing_newline(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line with newline\n")
    content = log.path.read_text(encoding="utf-8")
    assert content == "line with newline\n"
    assert not content.endswith("\n\n")


def test_append_hard_stops_at_max_bytes(tmp_path: Path) -> None:
    max_bytes = 16
    log = RawOverflowLog(tmp_path, "unit-1", max_bytes=max_bytes)

    assert log.append("1234567") is True  # 8 bytes with trailing newline
    assert log.append("abcdefg") is True  # 8 bytes with trailing newline
    assert log.append("overflow") is False

    assert log.path.stat().st_size == max_bytes
    assert log.path.read_text(encoding="utf-8") == "1234567\nabcdefg\n"


def test_size_bytes_returns_zero_before_first_write(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    assert not log.path.exists()
    assert log.size_bytes == 0


def test_size_bytes_uses_fast_path_after_first_write(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line one")
    expected = log.path.stat().st_size
    assert expected == len(b"line one\n")
    assert log.size_bytes == expected
    assert log.size_bytes == log._bytes_written


def test_size_bytes_returns_bytes_written_when_disabled(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("first line")
    log.disable()
    assert log.size_bytes == log._bytes_written


def test_size_bytes_returns_zero_when_file_missing_after_write(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("some content")
    assert log.path.exists()
    log.path.unlink()
    assert not log.path.exists()
    assert log.size_bytes == 0


def test_size_bytes_returns_zero_when_prior_run_file_exists(tmp_path: Path) -> None:
    log1 = RawOverflowLog(tmp_path, "unit-1")
    log1.append("prior run content")
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
