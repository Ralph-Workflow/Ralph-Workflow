from __future__ import annotations

import pytest

import ralph.testing.audit_skill_auto_commit as audit_module
from ralph.testing.audit_skill_auto_commit import main as audit_main

RALPH_TEST_CHANGE_REASONS = {
    "test_audit_blocks_regression_when_failure_path_log_removed": (
        "the skill auto-commit wiring moved out of cli/commands/run.py into "
        "cli/commands/_run_start_setup.py and the audit pins that live path, so "
        "the old run.py monkeypatch no longer removed a pinned literal and the "
        "regression went undetected"
    ),
    "test_audit_blocks_regression_when_phase_seam_skill_commit_resurfaces": (
        "the dead commit helper was removed (zero-dead-code rule), so the phase-seam guard "
        "now pins the live commit path instead"
    ),
}

pytestmark = pytest.mark.timeout_seconds(5)

_AUDIT_INVARIANTS = audit_module._INVARIANTS
_AUTO_COMMIT_SUBJECT = audit_module._SKILL_AUTO_COMMIT_SUBJECT
_SKILL_ROOT_PREFIXES = audit_module._SKILL_ROOT_PREFIXES


def _auto_commit_helper_module() -> str:
    helper_rel_paths = [
        inv.rel_path for inv in _AUDIT_INVARIANTS if _AUTO_COMMIT_SUBJECT in inv.present
    ]
    assert len(helper_rel_paths) == 1, (
        "the audit must pin exactly one invariant on the auto-commit helper module; "
        f"got {helper_rel_paths}"
    )
    root_package = audit_module.__name__.split(".", 1)[0]
    module_path = helper_rel_paths[0].removesuffix(".py").replace("/", ".")
    return f"{root_package}.{module_path}"


def _auto_commit_wiring_targets() -> tuple[tuple[str, str], ...]:
    helper_module = _auto_commit_helper_module()
    targets = tuple(
        (inv.rel_path, literal)
        for inv in _AUDIT_INVARIANTS
        for literal in inv.present
        if literal.startswith(f"from {helper_module} import ")
    )
    assert targets, f"the audit must pin at least one required import literal from {helper_module}"
    return targets


def test_audit_subject_literal_is_deterministic_chore_skills_sync_baseline_bundle() -> None:
    assert _AUTO_COMMIT_SUBJECT == "chore(skills): sync baseline bundle"


def test_audit_skill_root_prefixes_count_is_five() -> None:
    assert len(_SKILL_ROOT_PREFIXES) == 5
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
        == _SKILL_ROOT_PREFIXES
    )


def test_audit_invariants_cover_helper_module() -> None:
    invariant_paths = {inv.rel_path for inv in _AUDIT_INVARIANTS}
    assert "skills/_auto_commit.py" in invariant_paths


def test_audit_blocks_regression_when_helper_subject_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    real_read = audit_module._read
    agent_paths = "skills/_agent_paths.py"

    def _read_with_root_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == agent_paths:
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
    real_exists = audit_module._PACKAGE_ROOT.__class__.exists

    def _exists_with_helper_deleted(self: object) -> bool:
        if str(self).endswith("_auto_commit.py"):
            return False
        return real_exists(self)

    monkeypatch.setattr("pathlib.Path.exists", _exists_with_helper_deleted)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, f"Audit must exit 1 when _auto_commit.py is missing; got rc={rc}"
    assert "_auto_commit.py" in captured.out


def test_audit_blocks_regression_when_commit_review_skip_removed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_read = audit_module._read
    cleanup_path = "git/commit_review.py"

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
    real_read = audit_module._read
    targets = _auto_commit_wiring_targets()

    for rel_path, literal in targets:
        assert literal in real_read(rel_path), (
            f"{rel_path} must contain the required literal {literal!r} before the mutation"
        )

    clean_rc = audit_main([])
    clean_captured = capsys.readouterr()
    assert clean_rc == 0, (
        f"The audit must pass on the unmodified tree; got rc={clean_rc}: {clean_captured.out}"
    )

    def _read_with_wiring_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        for target_rel_path, literal in targets:
            if rel_path == target_rel_path:
                return content.replace(literal, "removed_rename")
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_wiring_removed)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, f"Audit must exit 1 when the wiring literal is removed; got rc={rc}"
    for rel_path, literal in targets:
        assert rel_path in captured.out, f"The audit output must name {rel_path}: {captured.out}"
        assert literal in captured.out, (
            f"The audit output must name the missing literal {literal!r}: {captured.out}"
        )
    assert "missing required literal" in captured.out


def test_audit_blocks_regression_when_phase_seam_skill_commit_resurfaces(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_read = audit_module._read
    runner_path = "pipeline/runner.py"

    def _read_with_seam_commit_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == runner_path:
            return (
                content
                + "\nfrom ralph.skills._auto_commit import commit_skill_writes  # regression\n"
            )
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_seam_commit_removed)
    rc = audit_main([])
    captured = capsys.readouterr()
    assert rc == 1, (
        f"Audit must exit 1 when the phase-seam skill commit is reintroduced; got rc={rc}"
    )
    assert runner_path in captured.out
    assert "forbidden literal" in captured.out
