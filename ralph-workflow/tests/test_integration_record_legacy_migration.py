"""One-way legacy-evidence migration tests.

A legacy on-disk :class:`IntegrationRecord` may carry diagnostic
evidence written by the pre-fix collector. The one-way migrator
inside :func:`ralph.pipeline.auto_integrate_record._parse_record_payload`
translates each legacy key into the new key space so the new
collector can re-verify it. Repository-global observations
(``for-each-ref`` / ``ls-files``) cannot be re-verified, so the
migrator surfaces intervention instead of silently clearing the
evidence. The common-dir branch reflog and ``logs/HEAD`` are
dropped with a ``repair_cap_reason`` marker.
"""

from __future__ import annotations

from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    _migrate_legacy_diagnostic_evidence,
    _parse_record_payload,
)


def test_legacy_work_key_migrates_to_unstaged_work() -> None:
    migrated, marker = _migrate_legacy_diagnostic_evidence((("work", "abc"),))
    assert dict(migrated).get("unstaged_work") == "abc"
    assert "work" not in {k for k, _ in migrated}
    assert marker is None


def test_legacy_controls_key_migrates_to_commit_controls() -> None:
    migrated, marker = _migrate_legacy_diagnostic_evidence((("controls", "abc"),))
    assert dict(migrated).get("commit_controls") == "abc"
    assert "controls" not in {k for k, _ in migrated}
    assert marker is None


def test_legacy_rev_parse_key_migrates_to_head() -> None:
    migrated, marker = _migrate_legacy_diagnostic_evidence((("rev-parse", "deadbeef"),))
    assert dict(migrated).get("head") == "deadbeef"
    assert marker is None


def test_legacy_merge_state_path_key_migrates_to_kind_prefixed_key() -> None:
    raw = (("/tmp/repo/.git/worktrees/wt/MERGE_HEAD", "abc"),)
    migrated, marker = _migrate_legacy_diagnostic_evidence(raw)
    keys = {k for k, _ in migrated}
    assert "merge_state_files:/tmp/repo/.git/worktrees/wt/MERGE_HEAD" in keys
    assert marker is None


def test_legacy_rebase_merge_dir_path_key_migrates_to_rebase_state() -> None:
    raw = (("/tmp/repo/.git/worktrees/wt/rebase-merge/git-rebase-todo", "abc"),)
    migrated, marker = _migrate_legacy_diagnostic_evidence(raw)
    keys = {k for k, _ in migrated}
    assert "rebase_state_files:/tmp/repo/.git/worktrees/wt/rebase-merge/git-rebase-todo" in keys
    assert marker is None


def test_legacy_rebase_apply_dir_path_key_migrates_to_rebase_state() -> None:
    raw = (("/tmp/repo/.git/worktrees/wt/rebase-apply/0001", "abc"),)
    migrated, marker = _migrate_legacy_diagnostic_evidence(raw)
    keys = {k for k, _ in migrated}
    assert any(k.startswith("rebase_state_files:") for k in keys)
    assert marker is None


def test_legacy_branch_reflog_observation_is_dropped_with_marker() -> None:
    raw = (("/tmp/repo/.git/logs/refs/heads/main", "abc"),)
    migrated, marker = _migrate_legacy_diagnostic_evidence(raw)
    assert marker == "legacy common-dir reflog observation dropped"
    assert all("/logs/refs/heads/" not in k for k, _ in migrated)


def test_legacy_logs_head_observation_is_dropped_with_marker() -> None:
    raw = (("/tmp/repo/.git/logs/HEAD", "abc"),)
    migrated, marker = _migrate_legacy_diagnostic_evidence(raw)
    assert marker == "legacy common-dir reflog observation dropped"
    assert all("/logs/HEAD" not in k for k, _ in migrated)


def test_legacy_arbitrary_path_is_preserved_under_original_key() -> None:
    """Unknown absolute paths are preserved verbatim so no on-disk evidence is silently lost."""
    raw = (("/tmp/some/random/observation", "abc"),)
    migrated, marker = _migrate_legacy_diagnostic_evidence(raw)
    assert dict(migrated).get("/tmp/some/random/observation") == "abc"
    assert marker is None


def test_legacy_for_each_ref_is_preserved_so_intervention_can_be_raised() -> None:
    """``for-each-ref`` is preserved verbatim so the loader sees it and surfaces intervention."""
    raw = (("for-each-ref", "deadbeef"),)
    migrated, marker = _migrate_legacy_diagnostic_evidence(raw)
    assert dict(migrated).get("for-each-ref") == "deadbeef"
    # The migrator itself does not raise; the loader does, because
    # the new collector cannot re-verify the observation.
    assert marker is None


def test_legacy_record_loads_with_migrated_keys() -> None:
    """An end-to-end legacy record loads with the new key space and a clean ownership field."""
    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (
            ("work", "old"),
            ("controls", "old"),
            ("/tmp/repo/.git/worktrees/wt/MERGE_HEAD", "x"),
            ("/tmp/repo/.git/logs/refs/heads/main", "drop"),
        ),
    }
    parsed = _parse_record_payload(legacy)
    assert isinstance(parsed, IntegrationRecord)
    keys = {k for k, _ in parsed.diagnostic_evidence}
    assert "unstaged_work" in keys
    assert "commit_controls" in keys
    assert "merge_state_files:/tmp/repo/.git/worktrees/wt/MERGE_HEAD" in keys
    # The common-dir reflog observation was dropped.
    assert "/tmp/repo/.git/logs/refs/heads/main" not in keys
    # The migrator recorded a reason for the drop.
    assert "legacy common-dir reflog observation dropped" in (parsed.repair_cap_reason or "")


def test_legacy_record_with_for_each_ref_loads_with_intervention_required() -> None:
    """A legacy record carrying ``for-each-ref`` loads as intervention-required."""
    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (("for-each-ref", "deadbeef"),),
    }
    parsed = _parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.diagnostic_intervention_required is True
    assert "legacy repository-global evidence" in (parsed.repair_cap_reason or "")


def test_legacy_record_with_ls_files_loads_with_intervention_required() -> None:
    """A legacy record carrying ``ls-files`` loads as intervention-required."""
    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (("ls-files", "abc"),),
    }
    parsed = _parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.diagnostic_intervention_required is True
    assert "legacy repository-global evidence" in (parsed.repair_cap_reason or "")


def test_legacy_record_with_protected_observations_present_loads() -> None:
    """Records with the new key space load without intervention even when evidence is sparse."""
    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (
            ("head", "h"),
            ("worktree_path", "/tmp/r"),
            ("index_scope", "absent"),
            ("unstaged_work", "u"),
            ("commit_controls", "c"),
        ),
    }
    parsed = _parse_record_payload(legacy)
    assert isinstance(parsed, IntegrationRecord)
    assert parsed.diagnostic_intervention_required is False
    assert parsed.repair_cap_reason is None
