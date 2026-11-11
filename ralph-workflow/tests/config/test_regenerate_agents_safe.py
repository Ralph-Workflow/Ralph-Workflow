"""Tests for safe regeneration of agents configuration via write_agents_toml."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from ralph.config.bootstrap import ensure_global_agents_config, regenerate_all

AGENTS_FILENAME = "ralph-workflow-agents.toml"


def test_ensure_global_agents_config_force_creates_backup_with_operator_edits(
    tmp_path: Path,
) -> None:
    """Calling ensure_global_agents_config(force=True) creates a .bak with operator edits."""
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    custom_content = '[agents.my_custom_agent]\ncmd = "custom-agent"\n'
    agents_file.write_text(custom_content, encoding="utf-8")

    result = ensure_global_agents_config(global_dir, force=True)

    bak_path = global_dir / f"{AGENTS_FILENAME}.bak"
    assert result.action == "regenerated"
    assert result.backup == bak_path
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == custom_content

    # Target file is rewritten and is valid TOML
    assert agents_file.exists()
    target_content = agents_file.read_text(encoding="utf-8")
    assert target_content != custom_content
    data = tomllib.loads(target_content)
    assert "agents" in data


def test_regenerate_all_creates_backup_with_operator_edits(
    tmp_path: Path,
) -> None:
    """regenerate_all creates a .bak backup containing previous operator edits."""
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    custom_content = '[agents.special]\ncmd = "special-agent"\n'
    agents_file.write_text(custom_content, encoding="utf-8")

    results = regenerate_all(global_dir=global_dir)

    agents_results = [r for r in results if r.path.name == AGENTS_FILENAME]
    assert len(agents_results) == 1
    assert agents_results[0].action == "regenerated"

    bak_path = global_dir / f"{AGENTS_FILENAME}.bak"
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == custom_content

    target_content = agents_file.read_text(encoding="utf-8")
    data = tomllib.loads(target_content)
    assert "agents" in data


def test_ensure_global_agents_config_force_logs_trigger(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ensure_global_agents_config(force=True) logs trigger 'ralph --regenerate-config'."""
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    agents_file.write_text('[agents.claude]\ncmd = "claude-old"\n', encoding="utf-8")

    with caplog.at_level(logging.INFO):
        ensure_global_agents_config(global_dir, force=True)

    assert "trigger: ralph --regenerate-config" in caplog.text
    assert str(agents_file) in caplog.text


def test_regenerate_all_logs_trigger(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """regenerate_all logs trigger 'ralph --regenerate-config' when refreshing agents file."""
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    agents_file.write_text('[agents.claude]\ncmd = "claude-old"\n', encoding="utf-8")

    with caplog.at_level(logging.INFO):
        regenerate_all(global_dir=global_dir)

    assert "trigger: ralph --regenerate-config" in caplog.text


def test_ensure_global_agents_config_validates_toml_before_replacing(
    tmp_path: Path,
) -> None:
    """If TOML validation fails, replacement fails without corrupting target."""
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    initial_content = '[agents.stable]\ncmd = "stable"\n'
    agents_file.write_text(initial_content, encoding="utf-8")

    with (
        patch(
            "ralph.config.bootstrap.write_agents_toml",
            side_effect=ValueError("Invalid TOML: unclosed table"),
        ),
        pytest.raises(ValueError, match="Invalid TOML"),
    ):
        ensure_global_agents_config(global_dir, force=True)

    # Existing file must remain untouched and no .bak created
    assert agents_file.read_text(encoding="utf-8") == initial_content
    assert not (global_dir / f"{AGENTS_FILENAME}.bak").exists()


def test_ensure_global_agents_config_commits_without_double_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When agents config is tracked, regeneration commits safely without double commit."""
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    agents_file.write_text('[agents.custom]\ncmd = "v1"\n', encoding="utf-8")

    committed_paths: list[list[str]] = []
    committed_subjects: list[str] = []

    def fake_classify(_target: Path, _context: Path) -> str:
        return "tracked"

    def fake_commit_deterministic_writes(
        _repo_root: Path,
        *,
        paths: list[str],
        pre_contents: dict[str, str | None],
        subject: str,
        create_commit_fn: object = None,
        stage_fn: object = None,
    ) -> object:
        from ralph.git.scoped_auto_commit import ScopedCommitResult, ScopedCommitStatus

        # Check if any path actually changed compared to pre_contents
        has_changes = any(
            pre_contents.get(p)
            != ((_repo_root / p).read_text(encoding="utf-8") if (_repo_root / p).exists() else None)
            for p in paths
        )
        if not has_changes:
            return ScopedCommitResult(ScopedCommitStatus.SKIPPED, sha=None, error=None)

        committed_paths.append(paths)
        committed_subjects.append(subject)
        return ScopedCommitResult(ScopedCommitStatus.CREATED, sha="12345678", error=None)

    def fake_capture(_repo_root: Path, paths: list[str]) -> dict[str, str | None]:
        return {
            p: (_repo_root / p).read_text(encoding="utf-8") if (_repo_root / p).exists() else None
            for p in paths
        }

    monkeypatch.setattr("ralph.config.bootstrap.classify_target_for_commit", fake_classify)
    monkeypatch.setattr(
        "ralph.config.bootstrap.commit_deterministic_writes", fake_commit_deterministic_writes
    )
    monkeypatch.setattr("ralph.git.scoped_auto_commit.capture_pre_write_contents", fake_capture)

    class FakeRepo:
        def __init__(self, _anchor: Path, search_parent_directories: bool = True) -> None:
            self.working_dir = str(tmp_path)

    monkeypatch.setattr("ralph.config.bootstrap.Repo", FakeRepo)

    # First run with force=True
    result = ensure_global_agents_config(global_dir, force=True)
    assert result.action == "regenerated"
    assert len(committed_subjects) == 1
    assert committed_subjects[0] == f"chore(config): update {AGENTS_FILENAME}"
    assert committed_paths[0] == [agents_file.relative_to(tmp_path).as_posix()]
    assert not any(p.endswith(".bak") for p in committed_paths[0])

    # Running again with force=True when content is identical does not create duplicate commit
    result2 = ensure_global_agents_config(global_dir, force=True)
    assert result2.action == "regenerated"
    assert result2.backup is None
    assert len(committed_subjects) == 1
