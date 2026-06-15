"""Black-box tests for ``auto_seed_default_gitignore`` in ``ralph.config.bootstrap``.

The function must be idempotent, preserve user-added lines, and cover the
prompt-named categories of common project structures (Python, Node, editor,
OS). All tests use ``tmp_path`` for I/O.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from ralph.config.bootstrap import (
    _DEFAULT_GITIGNORE_PATTERNS,
    auto_seed_default_gitignore,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_auto_seed_creates_gitignore_when_missing(tmp_path: Path) -> None:
    """Fresh tmp_path with no .gitignore: helper creates one and seeds every default pattern."""
    assert not (tmp_path / ".gitignore").exists()

    appended = auto_seed_default_gitignore(tmp_path)

    gitignore_path = tmp_path / ".gitignore"
    assert gitignore_path.exists(), ".gitignore must be created"
    content = gitignore_path.read_text(encoding="utf-8")
    for pattern in _DEFAULT_GITIGNORE_PATTERNS:
        assert pattern in content, f"Missing default pattern: {pattern!r}"
    assert appended == list(_DEFAULT_GITIGNORE_PATTERNS), (
        f"Expected all default patterns to be returned on a fresh repo; "
        f"got {len(appended)} patterns"
    )
    assert len(appended) == len(_DEFAULT_GITIGNORE_PATTERNS)


def test_auto_seed_is_idempotent(tmp_path: Path) -> None:
    """Calling the helper twice with the same repo_root must return [] on the second call."""
    gitignore_path = tmp_path / ".gitignore"
    gitignore_path.write_text("\n".join(_DEFAULT_GITIGNORE_PATTERNS) + "\n", encoding="utf-8")
    pre_sha = hashlib.sha256(gitignore_path.read_bytes()).hexdigest()

    appended = auto_seed_default_gitignore(tmp_path)

    assert appended == [], f"Expected empty list on idempotent call, got {appended!r}"
    post_sha = hashlib.sha256(gitignore_path.read_bytes()).hexdigest()
    assert pre_sha == post_sha, "File must be byte-identical after a no-op re-seed"


def test_auto_seed_preserves_user_patterns(tmp_path: Path) -> None:
    """A user-customized .gitignore with a custom line + the full default set is preserved."""
    gitignore_path = tmp_path / ".gitignore"
    user_line = "# my custom ignore"
    custom_block = "\n".join((user_line, *_DEFAULT_GITIGNORE_PATTERNS))
    gitignore_path.write_text(custom_block + "\n", encoding="utf-8")

    appended = auto_seed_default_gitignore(tmp_path)

    assert appended == [], f"Expected empty list, got {appended!r}"
    content_lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    assert content_lines[0] == user_line, (
        f"User-customized first line must be preserved; got: {content_lines[:3]!r}"
    )
    for pattern in _DEFAULT_GITIGNORE_PATTERNS:
        assert pattern in content_lines, f"Missing default pattern: {pattern!r}"


def test_auto_seed_covers_common_project_structures(tmp_path: Path) -> None:
    """The .gitignore covers the prompt-named categories: Python, Node, editor, OS."""
    auto_seed_default_gitignore(tmp_path)
    gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")

    assert "__pycache__/" in gitignore_text, f"Python category missing; got:\n{gitignore_text}"
    assert "node_modules/" in gitignore_text, f"Node category missing; got:\n{gitignore_text}"
    assert ".idea/" in gitignore_text, f"editor category missing; got:\n{gitignore_text}"
    assert ".DS_Store" in gitignore_text, f"OS category missing; got:\n{gitignore_text}"
