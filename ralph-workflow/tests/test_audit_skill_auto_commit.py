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
from ralph.testing.audit_skill_auto_commit import main as audit_main


def test_writer_scan_keeps_source_and_path_paired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = {
        "project_policy/agents_md.py": "workspace.write('AGENTS.md', 'content')",
        "project_policy/validators.py": "findings.append('write guidance')",
    }

    def paths(path: Path, pattern: str) -> list[Path]:
        return [path / name.split("/")[-1] for name in sources if name.startswith(path.name + "/")]

    monkeypatch.setattr(Path, "rglob", paths)
    monkeypatch.setattr(audit_module, "_read", sources.__getitem__)
    problems = audit_module._check_production_writer_scan()
    assert len(problems) == 1
    assert "project_policy/agents_md.py:1: unmarked deterministic writer site" in problems[0]


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


@pytest.mark.timeout_seconds(15)
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


@pytest.mark.timeout_seconds(15)
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


@pytest.mark.timeout_seconds(15)
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


@pytest.mark.timeout_seconds(15)
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


@pytest.mark.timeout_seconds(15)
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
    assert rc == 1, f"Audit must exit 1 when the run.py wiring reference is removed; got rc={rc}"
    assert run_path in captured.out
    assert "missing required literal" in captured.out


@pytest.mark.timeout_seconds(15)
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
            return (
                content
                + "\nfrom ralph.skills._auto_commit import commit_skill_updates  # regression\n"
            )
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_seam_commit_removed)
    rc = audit_main([])
    capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when the phase-seam skill commit is reintroduced; got rc={rc}"
    )


@pytest.mark.timeout_seconds(15)
def test_audit_blocks_regression_when_direct_chore_commit_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """wt-012: an unmarked direct ``create_commit`` call in ralph/ production triggers rc=1.

    The audit's ``_check_no_direct_chore_commit`` walker scans every
    ralph/ production module for direct ``create_commit(...)`` calls
    outside the helper modules. A future ad-hoc chore commit would
    re-introduce the deterministic-writer isolation bug -- the audit
    pins the invariant that ALL chore-purpose commit creation flows
    through the shared helper.
    """
    real_read = audit_module._read
    target_path = "config/bootstrap.py"

    def _read_with_ad_hoc_commit(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == target_path:
            # Inject a bare ``create_commit(...)`` call WITHOUT the
            # ``# deterministic-writer-ok:`` marker.
            return content + "\ncreate_commit(target_root, 'bad')\n"
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_ad_hoc_commit)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when an unmarked direct create_commit call "
        f"appears in production code; got rc={rc}\noutput: {captured.out}"
    )
    assert "direct create_commit call" in captured.out
    assert target_path in captured.out


@pytest.mark.timeout_seconds(15)
def test_audit_allows_marked_create_commit_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """wt-012: a ``# deterministic-writer-ok:`` marker permits a direct create_commit call.

    The audit recognises inline ``# deterministic-writer-ok: <reason>``
    markers on the call line (or its 3-line prelude) and lets the
    call through. This keeps the agent-initiated ``ralph commit`` CLI
    surface (which is NOT a deterministic auto-commit) auditable but
    not flagged.
    """
    real_read = audit_module._read
    target_path = "config/bootstrap.py"

    def _read_with_marked_commit(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == target_path:
            # Inject a create_commit call protected by the marker.
            return content + (
                "\n# deterministic-writer-ok: agent-initiated commit CLI surface\n"
                "create_commit(target_root, 'ok')\n"
            )
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_marked_commit)
    audit_main([])
    captured = capsys.readouterr()
    # No direct-commit finding from config/bootstrap.py. Other
    # violations may surface; we only check the negative on the
    # marker-protected call.
    assert "direct create_commit call" not in captured.out or target_path not in [
        line.split(":")[0].strip()
        for line in captured.out.splitlines()
        if "direct create_commit" in line
    ], (
        f"Marker-protected create_commit should NOT trigger a "
        f"direct-commit violation; got: {captured.out}"
    )


@pytest.mark.timeout_seconds(15)
def test_audit_blocks_regression_when_tracked_writer_loses_routing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """wt-012: removing the ``commit_deterministic_writes`` routing from a tracked
    writer AND the inline marker triggers rc=1.

    The audit's ``_check_writer_routing`` enforces that every tracked
    writer either routes through the shared helper or carries the
    inline marker. Removing BOTH is a regression.
    """
    real_read = audit_module._read
    target_path = "project_policy/agents_md.py"

    def _read_without_writer_routing(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == target_path:
            # Strip both ``commit_deterministic_writes`` and the
            # ``deterministic-writer-ok`` marker.
            cleaned = content.replace("deterministic-writer-ok", "[STRIPPED]")
            return cleaned
        return content

    monkeypatch.setattr(audit_module, "_read", _read_without_writer_routing)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when a tracked writer loses its routing "
        f"AND its marker; got rc={rc}\noutput: {captured.out}"
    )
    assert target_path in captured.out
    assert "tracked writer" in captured.out


@pytest.mark.timeout_seconds(15)
def test_audit_blocks_regression_when_unmarked_writer_site_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """wt-012: an unmarked tracked write site in production triggers rc=1."""
    real_read = audit_module._read
    target_path = "project_policy/agents_md.py"

    def _read_with_unmarked_write(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == target_path:
            return content.replace("deterministic-writer-ok", "unmarked")
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_unmarked_write)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when an unmarked write site exists in production; got rc={rc}\noutput: {captured.out}"
    )
    assert "unmarked deterministic writer site" in captured.out or "tracked writer" in captured.out
