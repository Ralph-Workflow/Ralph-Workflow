from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ralph.git.rebase.rebase_checkpoint import (
    RebaseCheckpoint,
    RebasePhase,
    clear_rebase_checkpoint,
    load_rebase_checkpoint,
    rebase_checkpoint_exists,
    save_rebase_checkpoint,
)


def _checkpoint_entry(upstream: str) -> RebaseCheckpoint:
    checkpoint = RebaseCheckpoint.new(upstream)
    checkpoint.set_phase(RebasePhase.RebaseInProgress)
    checkpoint.add_conflicted_file("conflict.txt")
    checkpoint.add_resolved_file("conflict.txt")
    checkpoint.record_error("initial failure")
    return checkpoint


def test_save_and_load_checkpoint_preserves_state(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    checkpoint = _checkpoint_entry("feature-branch")

    save_rebase_checkpoint(checkpoint)
    loaded = load_rebase_checkpoint()

    assert loaded is not None
    assert loaded.upstream_branch == "feature-branch"
    assert loaded.phase == RebasePhase.RebaseInProgress
    assert loaded.conflicted_files == ["conflict.txt"]
    assert loaded.resolved_files == ["conflict.txt"]
    assert loaded.error_count == checkpoint.error_count


def test_clear_rebase_checkpoint_removes_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    checkpoint = _checkpoint_entry("main")

    save_rebase_checkpoint(checkpoint)
    assert rebase_checkpoint_exists()

    clear_rebase_checkpoint()
    assert not rebase_checkpoint_exists()
    assert load_rebase_checkpoint() is None


def test_corrupted_checkpoint_restores_from_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    checkpoint = _checkpoint_entry("corrupt-branch")

    save_rebase_checkpoint(checkpoint)
    primary = Path(".agent") / "rebase_checkpoint.json"
    backup = primary.with_suffix(".json.bak")

    # Force the backup to match the latest checkpoint so restoration is deterministic.
    shutil.copy2(primary, backup)

    primary.write_text("{{corrupted json}}")

    restored = load_rebase_checkpoint()
    assert restored is not None
    assert restored.upstream_branch == "corrupt-branch"
    assert restored.phase == RebasePhase.RebaseInProgress
