"""Tests for subagent output capture and discovery strategies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.process.monitor import (
    FileSubagentOutputCapture,
    NullDiscoveryStrategy,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_file_capture_reads_new_lines_incrementally(tmp_path: Path) -> None:
    """FileSubagentOutputCapture returns only unread lines on each poll."""
    path = tmp_path / "capture.log"
    path.write_text("line one\nline two\n")

    capture = FileSubagentOutputCapture(str(path))
    assert capture.read_lines("w1") == ["line one", "line two"]

    with path.open("a") as fh2:
        fh2.write("line three\n")

    assert capture.read_lines("w1") == ["line three"]
    assert capture.read_lines("w1") == []


def test_file_capture_handles_partial_lines(tmp_path: Path) -> None:
    """A trailing partial line without a newline is preserved for the next poll."""
    path = tmp_path / "capture.log"
    path.write_text("complete line\npartial")

    capture = FileSubagentOutputCapture(str(path))
    assert capture.read_lines("w1") == ["complete line"]

    with path.open("a") as fh2:
        fh2.write(" continuation\n")

    assert capture.read_lines("w1") == ["partial continuation"]


def test_opencode_discovery_returns_empty_for_undocumented_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NullDiscoveryStrategy does not invent an undocumented path.

    Even when the legacy-looking directory layout is present on disk, the
    strategy returns an empty mapping because there is no documented path
    for any transport.
    """
    monkeypatch.chdir(tmp_path)
    worker_dir = tmp_path / ".agent" / "workers" / "w-1"
    worker_dir.mkdir(parents=True)
    log_file = worker_dir / "output.log"
    log_file.write_text("worker output\n")

    discovery = NullDiscoveryStrategy()
    assert discovery.discover_subagent_outputs(0) == {}


def test_claude_discovery_returns_empty_for_undocumented_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NullDiscoveryStrategy does not invent an undocumented path.

    Even when the legacy-looking directory layout is present on disk, the
    strategy returns an empty mapping because there is no documented path
    for any transport.
    """
    monkeypatch.chdir(tmp_path)
    worker_dir = tmp_path / ".claude" / "session" / "sess-1" / "worker-1"
    worker_dir.mkdir(parents=True)
    log_file = worker_dir / "log.txt"
    log_file.write_text("claude worker log\n")

    discovery = NullDiscoveryStrategy()
    assert discovery.discover_subagent_outputs(0) == {}


def test_discovery_returns_empty_when_no_logs(tmp_path: Path) -> None:
    """Discovery strategies return empty mapping when expected layout is absent."""
    discovery = NullDiscoveryStrategy()
    assert discovery.discover_subagent_outputs(0) == {}
