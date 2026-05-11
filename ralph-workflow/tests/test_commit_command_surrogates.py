"""Surrogate-safety regression tests for commit CLI plumbing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.cli.commands import commit as commit_module

if TYPE_CHECKING:
    from pathlib import Path


def test_working_tree_diff_strips_lone_surrogates(monkeypatch, tmp_path: Path) -> None:
    surrogate_diff = "diff\n+\udca4\n"

    class _FakeGit:
        def diff(self, *_args: object, **_kwargs: object) -> str:
            return surrogate_diff

    class _FakeHead:
        def is_valid(self) -> bool:
            return True

    class _FakeRepo:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.git = _FakeGit()
            self.head = _FakeHead()

    monkeypatch.setattr(commit_module, "Repo", _FakeRepo)

    diff = commit_module._working_tree_diff(tmp_path)

    assert "\udca4" not in diff
    diff.encode("utf-8")  # must not raise


def test_working_tree_diff_strips_surrogates_when_head_invalid(monkeypatch, tmp_path: Path) -> None:
    surrogate_diff = "diff cached\n+\udca4\n"

    class _FakeGit:
        def diff(self, *_args: object, **_kwargs: object) -> str:
            return surrogate_diff

    class _FakeHead:
        def is_valid(self) -> bool:
            return False

    class _FakeRepo:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.git = _FakeGit()
            self.head = _FakeHead()

    monkeypatch.setattr(commit_module, "Repo", _FakeRepo)

    diff = commit_module._working_tree_diff(tmp_path)

    assert "\udca4" not in diff
    diff.encode("utf-8")  # must not raise
