"""Regression tests: user-facing docs must not claim worktree-based parallel support.

These tests ensure that product documentation for same-workspace parallel workers v1
does not contain forbidden phrases that imply worktree isolation, per-worker branches,
or merge-back flows. Such language was explicitly retired in v1 and must not reappear.

A line containing a forbidden phrase is considered safe if that line OR the immediately
preceding line negates it (e.g., 'NOT supported', 'not used', 'never', 'do not', etc.).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_FORBIDDEN_PHRASES = [
    "git worktree",
    "per-worker branch",
    "per-worker checkout",
    "merge-back",
    "merge integrator",
    "separate git checkout",
    "worktree isolation",
    "per-worker repo",
]

_SAFETY_PHRASES = [
    "NOT supported",
    "not supported",
    "not used",
    "not part of",
    "never",
    "do not",
    "is not offered",
    "explicitly out of scope",
    "explicitly retired",
    "there are no",
    "no per-worker",
    "not a supported",
    "no separate",
    "no git",
]

_DOC_FILES = [
    "docs/agents/parallelization.md",
    "docs/migration/parallel-mode.md",
    "docs/architecture/parallel-fan-out.md",
    "ralph-workflow/README.md",
    "ralph-workflow/CONTRIBUTING.md",
    "README.md",
]


def _is_safe_line(line: str, prev_line: str) -> bool:
    """Return True if the line or its predecessor negates the forbidden phrase."""
    combined = (prev_line + " " + line).lower()
    return any(safety.lower() in combined for safety in _SAFETY_PHRASES)


@pytest.mark.parametrize("doc_path", _DOC_FILES)
def test_doc_has_no_forbidden_worktree_phrases(doc_path: str) -> None:
    """Doc must not claim worktree-based parallel support on any un-negated line."""
    full_path = _REPO_ROOT / doc_path
    if not full_path.exists():
        pytest.skip(f"Doc file not found (skipping): {doc_path}")

    text = full_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    violations: list[str] = []
    prev_line = ""
    for lineno, line in enumerate(lines, start=1):
        violations.extend(
            f"  {doc_path}:{lineno}: found {phrase!r} on line: {line.strip()!r}"
            for phrase in _FORBIDDEN_PHRASES
            if phrase.lower() in line.lower() and not _is_safe_line(line, prev_line)
        )
        prev_line = line

    assert violations == [], (
        "Found forbidden worktree-related phrases in user-facing docs.\n"
        "These phrases must be removed or clearly negated (e.g., 'NOT supported'):\n"
        + "\n".join(violations)
    )
