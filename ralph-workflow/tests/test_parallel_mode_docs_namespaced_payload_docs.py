"""Tests that the advanced-pipeline-configuration.md parallel section
accurately reflects same-workspace v1 behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

_DOC_PATH = Path(__file__).parent.parent / "docs" / "sphinx" / "advanced-pipeline-configuration.md"
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


_ARCH_DOC_PATH = (
    Path(__file__).parent.parent / "docs" / "sphinx" / "advanced-pipeline-configuration.md"
)


@pytest.fixture()
def arch_doc() -> str:
    return _ARCH_DOC_PATH.read_text(encoding="utf-8")


_EXTENDED_BANNED_PHRASES = [
    "worktree-based",
    "per-worker worktree",
    "parallel worktree",
    "merge-back",
    "merge integration",
    "worktree fan-out",
    "branch per worker",
    "per-worker branch",
    "worktree isolation",
    "worktree-based v1",
    "git worktrees simultaneously",
]

_WHITELIST_CONTAINS = [
    "docs/architecture/git-and-rebase.md",
    "ralph-workflow/ralph/git/operations.py",
    "tests/test_parallel_mode_docs.py",
]


def _collect_checked_md_files() -> list[Path]:
    repo_root = Path(__file__).parent.parent.parent
    rw_root = Path(__file__).parent.parent

    files: list[Path] = []

    sphinx_dir = rw_root / "docs" / "sphinx"
    if sphinx_dir.exists():
        files.extend(sphinx_dir.rglob("*.md"))

    arch_dir = repo_root / "docs" / "architecture"
    if arch_dir.exists():
        files.extend(arch_dir.rglob("*.md"))

    agents_dir = repo_root / "docs" / "agents"
    if agents_dir.exists():
        files.extend(agents_dir.rglob("*.md"))

    for name in ("README.md", "CONTRIBUTING.md", "AGENTS.md", "CLAUDE.md"):
        p = repo_root / name
        if p.exists():
            files.append(p)

    for name in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md"):
        p = rw_root / name
        if p.exists():
            files.append(p)

    return sorted(set(files))


def _is_whitelisted(path: Path) -> bool:
    path_str = str(path)
    return any(whitelist in path_str for whitelist in _WHITELIST_CONTAINS)


class TestNamespacedPayloadDocs:
    def test_parallel_mode_doc_mentions_worker_namespaced_payloads(self) -> None:
        """advanced-pipeline-configuration.md must document that per-worker prompt
        payloads are namespaced."""
        doc = _DOC_PATH.read_text(encoding="utf-8")
        assert ".agent/workers/<unit_id>/tmp/prompt_payloads/" in doc, (
            "advanced-pipeline-configuration.md must state that per-worker "
            "prompt payloads are written under "
            ".agent/workers/<unit_id>/tmp/prompt_payloads/ "
            "(concurrent-worker collision prevention)"
        )

    def test_parallel_mode_doc_no_future_tense_worktree(self) -> None:
        """advanced-pipeline-configuration.md must not describe worktree as a future
        or planned feature."""
        doc = _DOC_PATH.read_text(encoding="utf-8").lower()
        forbidden = [
            "future worktree",
            "planned worktree support",
            "worktree mode will be supported",
        ]
        violations = [phrase for phrase in forbidden if phrase in doc]
        assert violations == [], (
            f"advanced-pipeline-configuration.md contains future-tense "
            f"worktree language: {violations!r}"
        )
