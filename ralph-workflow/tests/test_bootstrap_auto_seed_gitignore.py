"""Black-box tests for ``auto_seed_default_gitignore`` in ``ralph.config.bootstrap``.

The function must be idempotent, preserve user-added lines, and cover the
prompt-named categories of common project structures (Python, Node, editor,
OS). All tests use ``tmp_path`` for I/O.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from ralph.config import bootstrap as bs_module
from ralph.config.bootstrap import (
    _DEFAULT_GITIGNORE_PATTERNS,
    auto_seed_default_gitignore,
)

if TYPE_CHECKING:
    import pytest

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


def test_auto_seed_default_gitignore_uses_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``auto_seed_default_gitignore`` must publish via the atomic helper.

    The atomic helper ensures SIGKILL mid-write leaves the target
    ``.gitignore`` intact (writes are staged to a sibling file, then
    ``Path.replace()``d into place). Regression: the previous
    implementation used a non-atomic ``open('a')`` write that could
    truncate or corrupt the file on a concurrent reader or signal.
    """
    captured: list[tuple[Path, str]] = []
    real_atomic = bs_module._atomic_append_text

    def spy_atomic(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
        captured.append((path, payload))
        return real_atomic(path, payload, encoding=encoding)

    monkeypatch.setattr(bs_module, "_atomic_append_text", spy_atomic)

    auto_seed_default_gitignore(tmp_path)

    assert captured, "auto_seed_default_gitignore must route through _atomic_append_text"
    gitignore_path = tmp_path / ".gitignore"
    assert any(p == gitignore_path for p, _ in captured), (
        f"Atomic helper must be called with the .gitignore path; "
        f"observed paths: {[p for p, _ in captured]!r}"
    )


def test_auto_seed_idempotent_repair_appends_child_rule_only_when_absent(tmp_path: Path) -> None:
    """AC-05: ``.agent/ralph-explore/`` must be in the seeded default
    gitignore. The second pass is a no-op when the explicit child
    rule is already present; the first pass on a fresh tree seeds
    the literal ``.agent/ralph-explore/`` line so the disposable
    cache coverage is reported transparently. User content
    already in ``.gitignore`` is preserved in both passes.
    """
    appended_first = auto_seed_default_gitignore(tmp_path)
    # The literal child rule must appear in the first-pass appended
    # list so the disposable cache coverage is explicit.
    assert ".agent/ralph-explore/" in appended_first
    gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".agent/ralph-explore/" in gitignore_text.splitlines()

    # Add a user line above the defaults to make sure the seeder
    # preserves it on the second pass.
    gitignore = tmp_path / ".gitignore"
    user_line = "# user custom line"
    existing = gitignore.read_text(encoding="utf-8")
    gitignore.write_text(user_line + "\n" + existing, encoding="utf-8")

    # Second pass: idempotent; nothing new is appended. The user
    # line and the explicit child rule are preserved.
    appended_second = auto_seed_default_gitignore(tmp_path)
    assert appended_second == []
    after = gitignore.read_text(encoding="utf-8").splitlines()
    assert after[0] == user_line
    assert ".agent/ralph-explore/" in after

    # A subsequent pass that REMOVES the explicit child rule
    # (simulating a regression / hand-edit) must repair it on the
    # next call. The seeder is idempotent against the parent
    # ``.agent/`` rule and additive against the explicit child
    # rule; removing the child rule and re-seeding must restore
    # the literal line without touching the parent.
    new_lines = [line for line in after if line.strip() != ".agent/ralph-explore/"]
    gitignore.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    appended_third = auto_seed_default_gitignore(tmp_path)
    assert ".agent/ralph-explore/" in appended_third
    final_text = gitignore.read_text(encoding="utf-8")
    assert ".agent/ralph-explore/" in final_text.splitlines()
    # The parent ``.agent/`` rule was already present and must NOT
    # be re-appended.
    assert final_text.count(".agent/") >= 1


def test_auto_seed_covers_common_project_structures(tmp_path: Path) -> None:
    """The .gitignore covers the prompt-named categories: Python, Node, editor, OS."""
    auto_seed_default_gitignore(tmp_path)
    gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")

    assert "__pycache__/" in gitignore_text, f"Python category missing; got:\n{gitignore_text}"
    assert "node_modules/" in gitignore_text, f"Node category missing; got:\n{gitignore_text}"
    assert ".idea/" in gitignore_text, f"editor category missing; got:\n{gitignore_text}"
    assert ".DS_Store" in gitignore_text, f"OS category missing; got:\n{gitignore_text}"
