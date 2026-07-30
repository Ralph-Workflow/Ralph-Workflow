from __future__ import annotations

import os
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


def test_find_latest_claude_transcript_entry_supports_workspace_paths_with_spaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace with spaces"
    workspace_root.mkdir()
    project_dir_name = str(workspace_root).replace("/", "-").replace(" ", "-")
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    expected_path = project_root / "session.jsonl"
    expected_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(transcript_module.Path, "home", lambda: tmp_path)

    entry = transcript_module.find_latest_claude_transcript_entry(workspace_root, min_mtime=0.0)

    assert entry == (expected_path, "session")


def test_find_latest_claude_transcript_entry_uses_project_scoped_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = str(workspace_root).replace("/", "-")
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    older = project_root / "older-session.jsonl"
    newer = project_root / "newer-session.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    os.utime(older, (10.0, 10.0))
    os.utime(newer, (20.0, 20.0))
    monkeypatch.setattr(transcript_module.Path, "home", lambda: tmp_path)

    entry = transcript_module.find_latest_claude_transcript_entry(workspace_root, min_mtime=15.0)

    assert entry == (newer, "newer-session")
    assert (
        transcript_module.find_latest_claude_transcript_entry(workspace_root, min_mtime=25.0)
        is None
    )
