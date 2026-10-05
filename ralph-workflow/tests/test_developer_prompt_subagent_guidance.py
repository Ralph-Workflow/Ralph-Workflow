"""Parallel execution guidance states safe ownership and waves."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"


def _source() -> str:
    return (_ROOT / "shared" / "_parallel_execution.jinja").read_text(encoding="utf-8")


def test_parallel_guidance_preserves_safe_dispatch_contract() -> None:
    source = _source()
    for text in ("independent units", "concurrently", "Never let two agents edit the same file", "main session"):
        assert text in source


def test_parallel_guidance_handles_paths_and_waves() -> None:
    source = _source()
    for text in ("Paths:", "Directories:", "Files:", "waves", "main session"):
        assert text in source
    assert ".agent" in source and ".git" in source and ".worktrees" in source


def test_parallel_guidance_serializes_conflicts_without_broadening_scope() -> None:
    source = _source()
    assert "Do not broaden file ownership" in source
    assert "Serialize conflicting ownership" in source
    assert "Continue your own ready work while results are pending" in source
