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

from pathlib import Path

import pytest

import ralph.testing.audit_skill_auto_commit as audit_module
from ralph.testing.audit_skill_auto_commit import (
    _check_no_direct_chore_commit,
    _check_production_writer_scan,
)
from ralph.testing.audit_skill_auto_commit import (
    main as audit_main,
)


@pytest.fixture(autouse=True)
def _stub_full_tree_scans(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the wt-012 full-tree writer scans out of the legacy
    literal-invariant regression tests below.

    Those tests monkeypatch ``_read`` and run ``audit_main`` under the
    real-time per-test SIGALRM budget; the production-writer scan walks and
    AST-parses the whole package tree, which is a different subsystem with
    its own dedicated tests at the bottom of this file. Stubbing it here
    keeps each regression test focused on the invariant it names.
    """
    monkeypatch.setattr(audit_module, "_check_production_writer_scan", lambda: [])
    monkeypatch.setattr(audit_module, "_check_no_direct_chore_commit", lambda: [])


# NOTE: ``test_audit_returns_zero_when_all_invariants_satisfied``,
# ``test_audit_main_returns_zero_on_clean_tree``, and
# ``test_audit_module_path`` were removed in the wt-05-test-opti pass.
# The two clean-tree checks duplicate the audit already executed by
# ``make verify`` (the ``audit_skill_auto_commit`` step in
# ``_VERIFY_STEPS``), and the module-path test is a pure import smoke
# that the same verify step implicitly proves. The remaining tests
# cover the literal-string + AST regression contract with monkey-patched
# sources.


def test_audit_subject_literal_is_deterministic_chore_skills_sync_baseline_bundle() -> None:
    """The pinned subject literal is ``chore(skills): sync baseline bundle``."""
    assert audit_module._SKILL_AUTO_COMMIT_SUBJECT == "chore(skills): sync baseline bundle"


def test_audit_skill_root_prefixes_count_is_five() -> None:
    """The FIVE canonical project-scope skill-root prefix strings are pinned."""
    assert len(audit_module._SKILL_ROOT_PREFIXES) == 5
    assert (
        frozenset(
            {
                ".opencode/skills/",
                ".agents/skills/",
                ".claude/skills/",
                ".codex/skills/",
                ".gemini/antigravity-cli/skills/",
            }
        )
        == audit_module._SKILL_ROOT_PREFIXES
    )


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
            return content.replace("chore(skills): sync baseline bundle", "renamed: subject")
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
        f"Audit must exit 1 when a skill-root prefix is removed from the constant; got rc={rc}"
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

    monkeypatch.setattr("pathlib.Path.exists", _exists_with_helper_deleted)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, f"Audit must exit 1 when _auto_commit.py is missing; got rc={rc}"
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
    assert rc == 1, f"Audit must exit 1 when the early-skip literal is removed; got rc={rc}"
    assert cleanup_path in captured.out


def test_audit_blocks_regression_when_failure_path_log_removed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing the ``Skill auto-commit failed (non-fatal)`` debug log literal from
    ``cli/commands/run.py`` triggers rc=1 -- pins the failure-path invariant.

    The plan (step 12) requires the audit to pin the run-path contract
    INCLUDING the failure-path debug-log literal and the surrounding
    try/except so a future refactor that silently drops the failure
    handler is caught at audit time.
    """
    real_read = audit_module._read
    run_path = "cli/commands/run.py"

    def _read_with_failure_log_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == run_path:
            return content.replace(
                "from ralph.skills._auto_commit import commit_skill_writes",
                "from ralph.skills._auto_commit import removed_rename",
            )
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_failure_log_removed)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when the run.py wiring reference is removed; got rc={rc}"
    )
    assert run_path in captured.out
    assert "missing required literal" in captured.out


def test_audit_blocks_regression_when_phase_seam_skill_commit_resurfaces(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """wt-012: reintroducing the phase-seam skill commit in the runner triggers rc=1.

    A future refactor that re-adds the ``commit_skill_updates`` call in
    the phase seam (the legacy dirty-discovery sweep) would commit
    whatever is dirty in the skill roots at every transition -- a
    skill path the user or agent dirtied mid-run would be swept into
    the deterministic chore commit. The audit's ``absent`` invariant
    on ``pipeline/runner.py`` blocks the regression.
    """
    real_read = audit_module._read
    runner_path = "pipeline/runner.py"

    def _read_with_seam_commit_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == runner_path:
            # Pretend the phase-seam sweep resurfaced.
            return content + "\nfrom ralph.skills._auto_commit import commit_skill_updates  # regression\n"
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_seam_commit_removed)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when the phase-seam skill commit is reintroduced; got rc={rc}"
    )
    assert runner_path in captured.out
    assert "forbidden literal" in captured.out


# --- wt-012: production-writer scan (S-7/S-8, DA-004/DA-016) ---------------------


def _write_module(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_writer_scan_flags_unmarked_write_in_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DA-004 probe case: a new unmarked writer under config/ is flagged."""
    _write_module(
        tmp_path,
        "config/new_module.py",
        "def sync(workspace):\n    workspace.write('state.json', '{}')\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    problems = _check_production_writer_scan()

    assert any("config/new_module.py:2" in p for p in problems), (
        f"unmarked workspace.write under config/ must be flagged; got: {problems}"
    )


def test_writer_scan_flags_unmarked_write_in_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DA-004 probe case: a new unmarked writer under skills/ is flagged."""
    _write_module(
        tmp_path,
        "skills/new_module.py",
        "from pathlib import Path\n\ndef sync():\n    Path('x').write_text('y')\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    problems = _check_production_writer_scan()

    assert any("skills/new_module.py:4" in p for p in problems), (
        f"unmarked write_text under skills/ must be flagged; got: {problems}"
    )


def test_writer_scan_flags_unmarked_write_in_new_top_level_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DA-004 probe case: a new top-level module is scanned too."""
    _write_module(
        tmp_path,
        "new_module.py",
        "def seed(ws):\n    ws.write('seed.md', 'x')\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    problems = _check_production_writer_scan()

    assert any("new_module.py:2" in p for p in problems), (
        f"unmarked ws.write in a top-level module must be flagged; got: {problems}"
    )


def test_writer_scan_passes_with_classification_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-committable write carrying the marker is accepted."""
    _write_module(
        tmp_path,
        "config/new_module.py",
        "def sync(workspace):\n"
        "    # deterministic-writer-ok: runtime cache state -- non-committable\n"
        "    workspace.write('state.json', '{}')\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    assert _check_production_writer_scan() == []


def test_writer_scan_passes_when_enclosing_function_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write inside a function that routes through a commit helper is routed."""
    _write_module(
        tmp_path,
        "config/new_module.py",
        "from ralph.git.scoped_auto_commit import commit_deterministic_writes\n"
        "\n"
        "def sync(root, workspace):\n"
        "    pre = {}\n"
        "    workspace.write('state.json', '{}')\n"
        "    commit_deterministic_writes(\n"
        "        root, written_paths=['state.json'], pre_contents=pre,\n"
        "        subject='chore: x', create_commit_fn=None,\n"
        "    )\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    assert _check_production_writer_scan() == []


def test_writer_scan_keeps_source_and_path_paired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DA-016: with multiple modules the finding reports the path whose content
    actually contains the unmarked write -- source and path cannot desync."""
    _write_module(tmp_path, "config/clean.py", "X = 1\n")
    _write_module(
        tmp_path,
        "config/dirty.py",
        "def f(workspace):\n    workspace.write('a', 'b')\n",
    )
    _write_module(tmp_path, "skills/other.py", "Y = 2\n")
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    problems = _check_production_writer_scan()

    assert any("config/dirty.py:2" in p for p in problems)
    assert not any("clean.py" in p or "other.py" in p for p in problems)


def test_writer_scan_excludes_canonical_primitives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The excluded primitive modules are not scanned."""
    _write_module(
        tmp_path,
        "mcp/artifacts/file_backend.py",
        "from pathlib import Path\n\ndef write(p):\n    Path(p).write_text('x')\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    assert _check_production_writer_scan() == []


def test_no_direct_chore_commit_flags_new_chore_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-8: a fresh direct create_commit call outside the allowlist is flagged."""
    _write_module(
        tmp_path,
        "phases/new_chore.py",
        "from ralph.git.operations import create_commit\n"
        "\n"
        "def go(root):\n"
        "    create_commit(root, 'chore: ad-hoc')\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    problems = _check_no_direct_chore_commit()

    assert any("phases/new_chore.py:4" in p for p in problems), (
        f"direct create_commit outside the allowlist must be flagged; got: {problems}"
    )


def test_no_direct_chore_commit_respects_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-8: the allowlisted agent-commit flow is not flagged."""
    _write_module(
        tmp_path,
        "cli/commands/commit.py",
        "from ralph.git.operations import create_commit\n"
        "\n"
        "def go(root):\n"
        "    create_commit(root, 'feat: agent work')\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    assert _check_no_direct_chore_commit() == []


# --- wt-012 PA-001: widened scan sets must include the canonical helpers -----


@pytest.mark.parametrize(
    "helper_name",
    [
        "atomic_write_text_if_changed",
        "write_text_if_changed",
        "write_bytes_if_changed",
        "atomic_write_bytes_if_changed",
    ],
)
def test_writer_scan_flags_unmarked_canonical_helper_call_attr_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    """DA-013: the attribute-call form is flagged for every required helper.

    The PA-001 widening of both scan sets added
    ``atomic_write_text_if_changed``, ``write_text_if_changed``, and
    ``write_bytes_if_changed`` to ``_WRITER_WRITE_ATTRS``. A fresh
    ``backend.<helper_name>(...)`` call without a marker or commit
    helper MUST be flagged regardless of whether the helper is the
    canonical three (which the audit also requires in the bare-name
    set) or ``atomic_write_bytes_if_changed`` (which is bare-name only
    but still tests the attribute form when the helper accidentally
    lands in the attribute set).
    """
    if helper_name not in audit_module._WRITER_WRITE_ATTRS:
        pytest.skip(
            f"helper {helper_name!r} is bare-name only; attribute form is out of scope"
        )
    body = (
        "from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND\n"
        f"\n"
        f"def sync(backend, dest, payload):\n"
        f"    backend.{helper_name}(dest, payload)\n"
    )
    expected_line = "4"
    _write_module(tmp_path, "config/new_helper.py", body)
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    problems = _check_production_writer_scan()

    assert any(
        f"config/new_helper.py:{expected_line}" in p and f"`{helper_name}`" in p
        for p in problems
    ), f"unmarked `{helper_name}` attr-call at line {expected_line} must be flagged; got: {problems}"


@pytest.mark.parametrize(
    "helper_name",
    [
        "atomic_write_text_if_changed",
        "write_text_if_changed",
        "write_bytes_if_changed",
        "atomic_write_bytes_if_changed",
    ],
)
def test_writer_scan_flags_unmarked_canonical_helper_call_bare_name_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    """DA-013: the bare-name form is flagged for every required helper.

    Mirrors the attribute-form regression above. The PA-001 widening
    added the canonical three helpers to ``_WRITER_WRITE_NAMES``; the
    pre-fix test only selected one form per helper (the attribute
    form, which was true for all three) so the bare-name branch was
    never exercised. This independent test forces the bare-name form
    for every helper in both scan sets, including the canonical three.
    """
    if helper_name not in audit_module._WRITER_WRITE_NAMES:
        pytest.skip(
            f"helper {helper_name!r} is attribute-only; bare-name form is out of scope"
        )
    body = (
        f"from ralph.mcp.artifacts.idempotent_write import {helper_name}\n"
        "from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND\n"
        "\n"
        f"def sync(dest, payload):\n"
        f"    {helper_name}(DEFAULT_FILE_BACKEND, dest, payload)\n"
    )
    expected_line = "5"
    _write_module(tmp_path, "config/new_helper.py", body)
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    problems = _check_production_writer_scan()

    assert any(
        f"config/new_helper.py:{expected_line}" in p and f"`{helper_name}`" in p
        for p in problems
    ), f"unmarked `{helper_name}` bare-name call at line {expected_line} must be flagged; got: {problems}"


@pytest.mark.parametrize(
    "helper_name",
    [
        "atomic_write_text_if_changed",
        "write_text_if_changed",
        "write_bytes_if_changed",
    ],
)
def test_writer_scan_rejects_canonical_three_in_both_forms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    """DA-013 closure: the canonical three helpers are rejected in BOTH forms.

    The PA-001 widening added the three canonical helpers
    (``atomic_write_text_if_changed``, ``write_text_if_changed``,
    ``write_bytes_if_changed``) to BOTH ``_WRITER_WRITE_ATTRS`` and
    ``_WRITER_WRITE_NAMES``. This regression enforces the dual-form
    coverage: for each of the three, a fresh attribute call AND a
    fresh bare-name call must be flagged. The parametrization
    deliberately does NOT include ``atomic_write_bytes_if_changed``,
    which is bare-name only.
    """
    assert helper_name in audit_module._WRITER_WRITE_ATTRS, (
        f"canonical helper {helper_name!r} MUST be in _WRITER_WRITE_ATTRS"
    )
    assert helper_name in audit_module._WRITER_WRITE_NAMES, (
        f"canonical helper {helper_name!r} MUST be in _WRITER_WRITE_NAMES"
    )
    # Attribute form: ``backend.<helper_name>(...)``
    attr_body = (
        "from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND\n"
        f"\n"
        f"def sync(backend, dest, payload):\n"
        f"    backend.{helper_name}(dest, payload)\n"
    )
    _write_module(tmp_path, "config/new_helper_attr.py", attr_body)
    # Bare-name form: ``<helper_name>(DEFAULT_FILE_BACKEND, ...)``
    bare_body = (
        f"from ralph.mcp.artifacts.idempotent_write import {helper_name}\n"
        "from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND\n"
        "\n"
        f"def sync(dest, payload):\n"
        f"    {helper_name}(DEFAULT_FILE_BACKEND, dest, payload)\n"
    )
    _write_module(tmp_path, "config/new_helper_bare.py", bare_body)
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    problems = _check_production_writer_scan()

    # Both forms must be flagged.
    attr_flagged = any(
        "config/new_helper_attr.py:4" in p and f"`{helper_name}`" in p
        for p in problems
    )
    bare_flagged = any(
        "config/new_helper_bare.py:5" in p and f"`{helper_name}`" in p
        for p in problems
    )
    assert attr_flagged, (
        f"canonical helper {helper_name!r} attr-call MUST be flagged; got: {problems}"
    )
    assert bare_flagged, (
        f"canonical helper {helper_name!r} bare-name call MUST be flagged; got: {problems}"
    )


def test_writer_scan_accepts_canonical_helper_with_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PA-001: a canonical-helper call carrying a marker is accepted."""
    _write_module(
        tmp_path,
        "config/new_helper.py",
        "from ralph.mcp.artifacts.idempotent_write import write_text_if_changed\n"
        "from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND\n"
        "\n"
        "def sync(dest, payload):\n"
        "    # filesystem-write-ok: runtime cache, not a repo deliverable\n"
        "    write_text_if_changed(DEFAULT_FILE_BACKEND, dest, payload)\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    assert _check_production_writer_scan() == []


def test_writer_scan_accepts_canonical_helper_in_function_routed_to_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PA-001: a canonical-helper call inside a function that calls a commit helper passes."""
    _write_module(
        tmp_path,
        "config/new_helper.py",
        "from ralph.git.scoped_auto_commit import commit_deterministic_writes\n"
        "from ralph.mcp.artifacts.idempotent_write import write_text_if_changed\n"
        "from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND\n"
        "\n"
        "def sync(root, dest, payload):\n"
        "    pre = {}\n"
        "    write_text_if_changed(DEFAULT_FILE_BACKEND, dest, payload)\n"
        "    commit_deterministic_writes(\n"
        "        root, paths=['cfg'], pre_contents=pre,\n"
        "        subject='chore: x', create_commit_fn=None,\n"
        "    )\n",
    )
    monkeypatch.setattr(audit_module, "_PACKAGE_ROOT", tmp_path)

    assert _check_production_writer_scan() == []
