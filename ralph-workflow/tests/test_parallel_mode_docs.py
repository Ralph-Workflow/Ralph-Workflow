"""Tests that parallel-mode.md accurately reflects same-workspace v1 behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

_DOC_PATH = Path(__file__).parent.parent / "docs" / "sphinx" / "parallel-mode.md"
_CONCEPTS_DOC_PATH = Path(__file__).parent.parent / "docs" / "sphinx" / "concepts.md"

_BANNED_PHRASES = [
    "worktree-based",
    "per-worker worktree",
    "merge-back",
    "merge integration",
    "parallel worktree",
]


@pytest.fixture()
def parallel_mode_doc() -> str:
    return _DOC_PATH.read_text(encoding="utf-8")


@pytest.fixture()
def concepts_doc() -> str:
    return _CONCEPTS_DOC_PATH.read_text(encoding="utf-8")


def test_parallel_mode_doc_exists() -> None:
    assert _DOC_PATH.is_file(), f"Missing doc: {_DOC_PATH}"


def test_concepts_doc_exists() -> None:
    assert _CONCEPTS_DOC_PATH.is_file(), f"Missing doc: {_CONCEPTS_DOC_PATH}"


def test_parallel_mode_doc_describes_same_workspace_mode(parallel_mode_doc: str) -> None:
    assert "same-workspace" in parallel_mode_doc.lower() or "same_workspace" in parallel_mode_doc


def test_parallel_mode_doc_does_not_mention_git_worktree_isolation(
    parallel_mode_doc: str,
) -> None:
    lower = parallel_mode_doc.lower()
    # "git worktree" (the git command) must not appear — we no longer use per-worker worktrees
    assert "git worktree" not in lower, (
        "parallel-mode.md must not reference 'git worktree' isolation (removed in v1)"
    )
    assert "own git worktree" not in lower, (
        "parallel-mode.md must not describe per-worker git worktrees (removed in v1)"
    )


def test_parallel_mode_doc_does_not_mention_merge_back(parallel_mode_doc: str) -> None:
    lower = parallel_mode_doc.lower()
    assert "merge back" not in lower, (
        "parallel-mode.md must not describe a merge-back step (removed in v1)"
    )
    assert "merge_integrat" not in lower, (
        "parallel-mode.md must not reference merge_integrator (removed in v1)"
    )


def test_parallel_mode_doc_mentions_allowed_directories(parallel_mode_doc: str) -> None:
    assert "allowed_directories" in parallel_mode_doc, (
        "parallel-mode.md must document allowed_directories (required in v1)"
    )


def test_parallel_mode_doc_mentions_worker_artifact_evidence(parallel_mode_doc: str) -> None:
    assert "artifact" in parallel_mode_doc.lower(), (
        "parallel-mode.md must explain worker success is determined by artifact evidence"
    )


def test_parallel_mode_doc_mentions_worker_namespace(parallel_mode_doc: str) -> None:
    assert ".agent/workers" in parallel_mode_doc, (
        "parallel-mode.md must mention .agent/workers/ as the per-worker namespace"
    )


def test_parallel_mode_doc_does_not_mention_git_status_fallback(parallel_mode_doc: str) -> None:
    lower = parallel_mode_doc.lower()
    assert "git status" not in lower, (
        "parallel-mode.md must not mention git status as a success signal (removed in v1)"
    )


@pytest.mark.parametrize("phrase", _BANNED_PHRASES)
def test_parallel_mode_doc_does_not_contain_banned_phrase(
    parallel_mode_doc: str, phrase: str
) -> None:
    assert phrase.lower() not in parallel_mode_doc.lower(), (
        f"parallel-mode.md must not contain banned phrase {phrase!r} (removed in v1)"
    )


@pytest.mark.parametrize("phrase", _BANNED_PHRASES)
def test_concepts_doc_does_not_contain_banned_phrase(concepts_doc: str, phrase: str) -> None:
    assert phrase.lower() not in concepts_doc.lower(), (
        f"concepts.md must not contain banned phrase {phrase!r} (removed in v1)"
    )


_ARCH_DOC_PATH = (
    Path(__file__).parent.parent.parent / "docs" / "architecture" / "parallel-fan-out.md"
)


@pytest.fixture()
def arch_doc() -> str:
    return _ARCH_DOC_PATH.read_text(encoding="utf-8")


def test_arch_doc_exists() -> None:
    assert _ARCH_DOC_PATH.is_file(), f"Missing doc: {_ARCH_DOC_PATH}"


def test_arch_doc_does_not_use_git_worktree_as_command(arch_doc: str) -> None:
    """Architecture doc must not describe git worktree as a command we run."""
    lower = arch_doc.lower()
    assert "git worktree add" not in lower, (
        "parallel-fan-out.md must not describe 'git worktree add' (no per-worker worktrees in v1)"
    )
    assert "git worktree remove" not in lower, (
        "parallel-fan-out.md must not describe 'git worktree remove' "
        "(no per-worker worktrees in v1)"
    )


def test_arch_doc_does_not_describe_merge_back_step(arch_doc: str) -> None:
    """Architecture doc must not describe a merge-back step."""
    lower = arch_doc.lower()
    assert "merge back" not in lower, (
        "parallel-fan-out.md must not describe a 'merge back' step (removed in v1)"
    )
    assert "merge_integrat" not in lower, (
        "parallel-fan-out.md must not reference merge_integrator (removed in v1)"
    )


def test_arch_doc_mentions_same_workspace(arch_doc: str) -> None:
    assert "same" in arch_doc.lower() and "workspace" in arch_doc.lower(), (
        "parallel-fan-out.md must describe same-workspace execution"
    )


def test_arch_doc_mentions_allowed_directories(arch_doc: str) -> None:
    assert "allowed_directories" in arch_doc, (
        "parallel-fan-out.md must document allowed_directories (path isolation mechanism)"
    )
