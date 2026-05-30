from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import _pty_transcript as transcript_module

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_find_claude_transcript_entry_supports_multiple_session_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / ".claude" / "projects"
    first_project = projects_root / "b-project"
    second_project = projects_root / "a-project"
    first_project.mkdir(parents=True)
    second_project.mkdir(parents=True)
    expected_path = second_project / "real-session.jsonl"
    expected_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(transcript_module.Path, "home", lambda: tmp_path)

    entry = transcript_module.find_claude_transcript_entry(("wrong-session", "real-session"))

    assert entry == (expected_path, "real-session")


def test_find_claude_transcript_path_preserves_single_session_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / ".claude" / "projects" / "project"
    projects_root.mkdir(parents=True)
    expected_path = projects_root / "session-123.jsonl"
    expected_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(transcript_module.Path, "home", lambda: tmp_path)

    observed = transcript_module.find_claude_transcript_path("session-123")

    assert observed == expected_path
