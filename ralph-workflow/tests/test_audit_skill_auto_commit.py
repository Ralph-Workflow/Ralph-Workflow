"""Tests for ``ralph.testing.audit_skill_auto_commit``.

The audit pins the wt-025 deterministic skill-update auto-commit contract:

* the literal subject ``chore(skills): sync baseline bundle``,
* the FIVE canonical project-scope skill-root prefix set,
* the AST placement of the early-skip block in
  ``ralph/git/commit_cleanup.py::untrack_engine_internal_files``, and
* the existence of ``ralph/skills/_auto_commit.py``.

Mirrors the structure of ``tests/test_audit_parallelization_dormant.py``
(PA-006 closure): smoke tests, structural invariant tests, and one
regression test per invariant target that monkey-patches the audit's
``_read`` to remove a literal, asserting the audit returns 1 and emits a
labeled violation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.testing.audit_skill_auto_commit as audit_module
from ralph.testing.audit_skill_auto_commit import main as audit_main

if TYPE_CHECKING:
    import pytest


def test_audit_returns_zero_when_all_invariants_satisfied() -> None:
    """Smoke test: on a clean tree, the audit exits 0."""
    assert audit_main([]) == 0


def test_audit_main_returns_zero_on_clean_tree() -> None:
    """Second smoke test that exits with rc 0 and prints the OK summary."""
    rc = audit_main([])
    assert rc == 0


def test_audit_module_path() -> None:
    """The audit module exposes ``main`` for ``python -m`` invocation."""
    assert hasattr(audit_module, "main")
    assert hasattr(audit_module, "_SKILL_AUTO_COMMIT_SUBJECT")
    assert hasattr(audit_module, "_SKILL_ROOT_PREFIXES")


def test_audit_subject_literal_is_deterministic_chore_skills_sync_baseline_bundle() -> None:
    """The pinned subject literal is ``chore(skills): sync baseline bundle``."""
    assert audit_module._SKILL_AUTO_COMMIT_SUBJECT == "chore(skills): sync baseline bundle"


def test_audit_skill_root_prefixes_count_is_five() -> None:
    """The FIVE canonical project-scope skill-root prefix strings are pinned."""
    assert len(audit_module._SKILL_ROOT_PREFIXES) == 5
    assert frozenset(
        {
            ".opencode/skills/",
            ".agents/skills/",
            ".claude/skills/",
            ".codex/skills/",
            ".gemini/antigravity-cli/skills/",
        }
    ) == audit_module._SKILL_ROOT_PREFIXES


def test_audit_invariants_cover_helper_module() -> None:
    """The audit's ``_INVARIANTS`` tuple contains the helper-module invariant."""
    invariant_paths = {inv.rel_path for inv in audit_module._INVARIANTS}
    assert "skills/_auto_commit.py" in invariant_paths


def test_audit_blocks_regression_when_helper_subject_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing the subject literal from the helper module triggers rc=1."""
    real_read = audit_module._read
    helper_path = "skills/_auto_commit.py"

    def _read_with_subject_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == helper_path:
            return content.replace(
                "chore(skills): sync baseline bundle", "renamed: subject"
            )
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_subject_removed)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, f"Audit must exit 1 when the subject literal is renamed; got rc={rc}"
    assert helper_path in captured.out
    assert "missing required literal" in captured.out


def test_audit_blocks_regression_when_skill_root_prefix_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing one of the FIVE skill-root prefixes from the agent_paths file triggers rc=1."""
    real_read = audit_module._read
    agent_paths = "skills/_agent_paths.py"

    def _read_with_root_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == agent_paths:
            # Remove ".agents/skills/" from the set
            return content.replace('".agents/skills/",', "")
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_root_removed)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when a skill-root prefix is removed from the constant; "
        f"got rc={rc}"
    )
    assert agent_paths in captured.out


def test_audit_blocks_regression_when_helper_module_deleted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deleting ``_auto_commit.py`` triggers the file-existence check rc=1."""
    real_exists = audit_module._PACKAGE_ROOT.__class__.exists

    def _exists_with_helper_deleted(self: object) -> bool:
        # Pretend the helper module is missing
        if str(self).endswith("_auto_commit.py"):
            return False
        return real_exists(self)

    monkeypatch.setattr(
        "pathlib.Path.exists", _exists_with_helper_deleted
    )
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when _auto_commit.py is missing; got rc={rc}"
    )
    assert "_auto_commit.py" in captured.out


def test_audit_blocks_regression_when_commit_cleanup_skip_removed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing the literal-string skip block from commit_cleanup.py triggers rc=1."""
    real_read = audit_module._read
    cleanup_path = "git/commit_cleanup.py"

    def _read_with_skip_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == cleanup_path:
            return content.replace("Skipping tracked skill-root path", "Removed marker")
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_skip_removed)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when the early-skip literal is removed; got rc={rc}"
    )
    assert cleanup_path in captured.out
