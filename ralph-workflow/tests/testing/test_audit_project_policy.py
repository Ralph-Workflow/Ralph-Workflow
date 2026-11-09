"""Tests for :mod:`ralph.testing.audit_project_policy`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.language_detector.models import ProjectStack
from ralph.testing.audit_project_policy import collect_violations, main
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


def _seed_ready_fs(root: Path) -> None:
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    ws = FsWorkspace(root)
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))


def test_collect_violations_reports_missing_for_empty_dir(tmp_path: Path) -> None:
    violations = collect_violations(tmp_path)
    assert len(violations) > 0
    assert any("AGENTS.md" in v for v in violations)


def test_main_exits_one_on_violations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ralph.testing.audit_project_policy.REPO_ROOT",
        tmp_path,
    )
    exit_code = main([])
    assert exit_code == 1


def test_collect_violations_clean_on_seeded_workspace(tmp_path: Path) -> None:
    _seed_ready_fs(tmp_path)
    violations = collect_violations(tmp_path)
    assert violations == []


def test_main_exits_zero_on_clean_workspace(tmp_path: Path, monkeypatch) -> None:
    _seed_ready_fs(tmp_path)
    monkeypatch.setattr(
        "ralph.testing.audit_project_policy.REPO_ROOT",
        tmp_path,
    )
    exit_code = main([])
    assert exit_code == 0
