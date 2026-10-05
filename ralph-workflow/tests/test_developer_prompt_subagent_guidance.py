"""Parallel execution guidance locks safe ownership and conflict handling."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"


def _source() -> str:
    return (_ROOT / "shared" / "_parallel_execution.jinja").read_text(encoding="utf-8")


def test_parallel_guidance_preserves_safe_dispatch_contract() -> None:
    source = _source()
    for text in ("independent units", "concurrently", "Never let two agents edit the same file", "main session"):
        assert text in source


def test_parallel_guidance_names_units_paths_and_directories() -> None:
    source = _source()
    for text in ("Paths:", "Directories:", "unit", "main session"):
        assert text in source


def test_parallel_guidance_serializes_ownership_conflicts_without_broadening() -> None:
    source = _source()
    assert "When two ready units claim overlapping paths" in source
    assert "serialize the conflicting units in dependency order" in source
    assert "never widen a worker's file scope" in source
    assert "re-cut the units" in source


def test_parallel_guidance_excludes_pipeline_state_paths() -> None:
    source = _source()
    assert "Worker scope never includes" in source
    assert ".agent" in source
    assert ".git" in source
    assert ".worktrees" in source
