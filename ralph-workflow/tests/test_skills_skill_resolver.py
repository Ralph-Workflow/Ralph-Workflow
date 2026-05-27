"""Tests for ralph.skills._skill_resolver."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.skills._skill_resolver import get_inline_skill_content

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_returns_empty_when_env_var_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.delenv("RALPH_INLINE_SKILLS_DIR", raising=False)
    assert get_inline_skill_content() == ""


def test_returns_empty_when_dir_not_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path / "missing"))
    assert get_inline_skill_content() == ""


def test_returns_empty_when_dir_is_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path))
    assert get_inline_skill_content() == ""


def test_returns_empty_when_only_process_dir_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "b.md").write_text("BBB", encoding="utf-8")
    (tmp_path / "a.md").write_text("AAA", encoding="utf-8")
    monkeypatch.setenv("RALPH_SKILLS_PROCESS_DIR", str(tmp_path))
    monkeypatch.delenv("RALPH_INLINE_SKILLS_DIR", raising=False)
    assert get_inline_skill_content() == ""


def test_returns_combined_content_from_md_files_in_sorted_order_when_inline_dir_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "b.md").write_text("BBB", encoding="utf-8")
    (tmp_path / "a.md").write_text("AAA", encoding="utf-8")
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path))
    assert get_inline_skill_content() == "AAA\n\n---\n\nBBB"


def test_ignores_non_md_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "skip.txt").write_text("NOPE", encoding="utf-8")
    (tmp_path / "keep.md").write_text("KEEP", encoding="utf-8")
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path))
    assert get_inline_skill_content() == "KEEP"
