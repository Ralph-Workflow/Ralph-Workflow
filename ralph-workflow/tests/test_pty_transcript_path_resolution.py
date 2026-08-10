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


def test_existing_transcript_names_snapshots_pre_existing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = str(workspace_root).replace("/", "-")
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    (project_root / "orchestrator-session.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(transcript_module.Path, "home", lambda: tmp_path)

    names = transcript_module.existing_transcript_names(workspace_root)

    assert names == frozenset({"orchestrator-session.jsonl"})


def test_existing_transcript_names_returns_empty_set_when_project_dir_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setattr(transcript_module.Path, "home", lambda: tmp_path)

    assert transcript_module.existing_transcript_names(workspace_root) == frozenset()


def test_find_latest_claude_transcript_entry_excludes_pre_existing_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wt-04-claude-parsing regression: a still-active sibling session must
    never masquerade as the freshly-spawned child.

    The orchestrating session and any child ``claude`` process it spawns
    in the same workspace share the exact same
    ``~/.claude/projects/<project-key>`` directory. Before this fix,
    ``find_latest_claude_transcript_entry`` picked whichever ``*.jsonl``
    file had the newest mtime among ALL files touched since the child's
    start -- including an orchestrator session that already existed and
    keeps being appended to throughout the child's entire run. That
    starves the transcript-tail thread of every real event the child
    actually produced (the observed symptom: "session ID was not
    observed", "no tool activity was observed", "subagent dispatch was
    not observed" despite the child doing real, verified work).
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = str(workspace_root).replace("/", "-")
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    orchestrator_session = project_root / "orchestrator-session.jsonl"
    orchestrator_session.write_text("{}\n", encoding="utf-8")
    os.utime(orchestrator_session, (10.0, 10.0))
    monkeypatch.setattr(transcript_module.Path, "home", lambda: tmp_path)

    # The reader snapshots existing names BEFORE the child starts.
    pre_existing = transcript_module.existing_transcript_names(workspace_root)
    assert pre_existing == frozenset({"orchestrator-session.jsonl"})

    # The child process starts; its transcript file appears.
    child_session = project_root / "child-session.jsonl"
    child_session.write_text("{}\n", encoding="utf-8")
    os.utime(child_session, (20.0, 20.0))

    # The orchestrator keeps being active too -- its mtime advances past
    # the child's, which is exactly the race that broke the old
    # "latest mtime wins" heuristic.
    os.utime(orchestrator_session, (30.0, 30.0))

    without_exclusion = transcript_module.find_latest_claude_transcript_entry(
        workspace_root, min_mtime=15.0
    )
    assert without_exclusion == (orchestrator_session, "orchestrator-session"), (
        "sanity check: without the fix the orchestrator's file wins on raw mtime"
    )

    with_exclusion = transcript_module.find_latest_claude_transcript_entry(
        workspace_root, min_mtime=15.0, exclude_names=pre_existing
    )
    assert with_exclusion == (child_session, "child-session")
