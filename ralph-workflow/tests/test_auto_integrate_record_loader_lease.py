from __future__ import annotations

from pathlib import Path


def test_parse_lease_extracts_fingerprint_and_held_at() -> None:
    from ralph.pipeline.auto_integrate_record import parse_lease

    assert parse_lease("abc:1234.5") == ("abc", 1234.5)


def test_parse_lease_returns_none_for_malformed() -> None:
    from ralph.pipeline.auto_integrate_record import parse_lease

    assert parse_lease("abc") == (None, None)
    assert parse_lease("") == (None, None)
    assert parse_lease(None) == (None, None)
    assert parse_lease(":1234") == (None, None)
    assert parse_lease("abc:") == (None, None)
    assert parse_lease("abc:not-a-number") == (None, None)


def test_load_time_counter_clamp_clamps_to_cap_minus_one() -> None:
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "merge_commit_repair_attempts": 541,
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.merge_commit_repair_attempts == 4
    assert parsed.diagnostic_intervention_required is False


def test_load_time_counter_clamp_preserves_under_cap_values() -> None:
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "merge_commit_repair_attempts": 2,
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.merge_commit_repair_attempts == 2


def test_load_time_counter_clamp_handles_zero_and_missing() -> None:
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.merge_commit_repair_attempts == 0


def test_load_time_counter_clamp_treats_garbage_as_rejected() -> None:
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "merge_commit_repair_attempts": "not-an-int",
    }
    assert parse_record_payload(legacy) is None


def test_migrate_payload_unparseable_lease_ownership_clears_fields() -> None:
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (("unstaged_work", "changes"),),
        "diagnostic_ownership": "legacy_fingerprint_without_colon",
        "diagnostic_intervention_required": True,
        "repair_cap_reason": "diagnostic repair exhausted; operator intervention required",
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_ownership is None
    assert parsed.diagnostic_intervention_required is False
    assert parsed.repair_cap_reason is None
    assert next_integration_action(parsed) != IntegrationAction.REQUIRE_INTERVENTION


def test_migrate_payload_legacy_global_keys_clears_fields() -> None:
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (("for-each-ref", "refs/heads/main"), ("ls-files", "file.txt")),
        "diagnostic_ownership": "owner:12345.0",
        "diagnostic_intervention_required": True,
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_ownership is None
    assert parsed.diagnostic_intervention_required is False
    assert next_integration_action(parsed) != IntegrationAction.REQUIRE_INTERVENTION


def test_migrate_payload_stale_fingerprint_lease_clears_fields() -> None:
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (("unstaged_work", "dirty"),),
        "diagnostic_ownership": "mismatched_stale_fingerprint:12345.0",
        "diagnostic_intervention_required": True,
        "repair_cap_reason": "diagnostic repair exhausted; operator intervention required",
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_ownership is None
    assert parsed.diagnostic_intervention_required is False
    assert parsed.repair_cap_reason is None
    assert next_integration_action(parsed) != IntegrationAction.REQUIRE_INTERVENTION


def test_old_format_record_without_owner_fields_round_trips() -> None:
    from ralph.pipeline.auto_integrate_record import parse_lease, parse_record_payload
    from ralph.pipeline.operation_evidence import identity_fingerprint

    base_record = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "/tmp/wt",
        "operation_kind": "feature_integrate",
    }
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

    temp_rec = IntegrationRecord.model_validate(base_record)
    fp = identity_fingerprint(temp_rec)
    lease = f"{fp}:1700000000.0"

    old_format: dict[str, object] = {
        **base_record,
        "diagnostic_evidence": [["unstaged_work", "clean"]],
        "diagnostic_ownership": lease,
    }
    parsed = parse_record_payload(old_format)
    assert parsed is not None
    assert parsed.diagnostic_ownership == lease
    assert parsed.diagnostic_owner_pid is None
    assert parsed.diagnostic_owner_boot_id is None
    assert parse_lease(parsed.diagnostic_ownership) == (fp, 1700000000.0)


def test_new_format_record_with_owner_fields_round_trips() -> None:
    import os

    from ralph.pipeline.auto_integrate_record import (
        IntegrationRecord,
        parse_lease,
        parse_record_payload,
    )
    from ralph.pipeline.operation_evidence import identity_fingerprint

    base_record: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "/tmp/wt",
        "operation_kind": "feature_integrate",
    }
    temp_rec = IntegrationRecord.model_validate(base_record)
    fp = identity_fingerprint(temp_rec)
    lease = f"{fp}:1700000000.0"

    new_format: dict[str, object] = {
        **base_record,
        "diagnostic_evidence": [["unstaged_work", "clean"]],
        "diagnostic_ownership": lease,
        "diagnostic_owner_pid": os.getpid(),
        "diagnostic_owner_boot_id": None,
    }
    parsed = parse_record_payload(new_format)
    assert parsed is not None
    assert parsed.diagnostic_ownership == lease
    assert parsed.diagnostic_owner_pid == os.getpid()
    assert parsed.diagnostic_owner_boot_id is None
    assert parse_lease(parsed.diagnostic_ownership) == (fp, 1700000000.0)


def test_migrate_payload_unknown_evidence_keys_clears_fields() -> None:
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    payload = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": [
            ["unstaged_work", "clean"],
            ["completely_unrecognized_key_from_future", "value"],
        ],
        "diagnostic_ownership": "owner:12345.0",
        "diagnostic_intervention_required": True,
        "repair_cap_reason": "diagnostic repair exhausted; operator intervention required",
    }
    parsed = parse_record_payload(payload)
    assert parsed is not None
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_ownership is None
    assert parsed.diagnostic_intervention_required is False
    assert parsed.repair_cap_reason is None
    assert next_integration_action(parsed) != IntegrationAction.REQUIRE_INTERVENTION


def test_migrate_payload_unparseable_evidence_format_clears_fields() -> None:
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    payload = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": "corrupted_non_sequence_evidence_string",
        "diagnostic_ownership": "owner:12345.0",
        "diagnostic_intervention_required": True,
    }
    parsed = parse_record_payload(payload)
    assert parsed is not None
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_ownership is None
    assert parsed.diagnostic_intervention_required is False
    assert next_integration_action(parsed) != IntegrationAction.REQUIRE_INTERVENTION


def test_dead_owner_pid_releases_hold_at_load_time() -> None:
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        IntegrationRecord,
        parse_record_payload,
    )
    from ralph.pipeline.operation_evidence import identity_fingerprint

    base_record = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "/tmp/wt",
        "operation_kind": "feature_integrate",
    }
    temp_rec = IntegrationRecord.model_validate(base_record)
    fp = identity_fingerprint(temp_rec)

    dead_pid = 999999999
    payload = {
        **base_record,
        "diagnostic_evidence": [["unstaged_work", "clean"]],
        "diagnostic_ownership": f"{fp}:1700000000.0",
        "diagnostic_owner_pid": dead_pid,
        "diagnostic_intervention_required": True,
    }
    parsed = parse_record_payload(payload)
    assert parsed is not None
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_ownership is None
    assert parsed.diagnostic_owner_pid is None
    assert parsed.diagnostic_intervention_required is False
    assert next_integration_action(parsed) != IntegrationAction.REQUIRE_INTERVENTION


def test_dead_owner_boot_id_releases_hold_at_load_time() -> None:
    from ralph.pipeline._auto_integrate_record_restamp import current_boot_id
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        IntegrationRecord,
        parse_record_payload,
    )
    from ralph.pipeline.operation_evidence import identity_fingerprint

    live_boot_id = current_boot_id()
    if live_boot_id is None:
        return

    base_record = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "/tmp/wt",
        "operation_kind": "feature_integrate",
    }
    temp_rec = IntegrationRecord.model_validate(base_record)
    fp = identity_fingerprint(temp_rec)

    payload = {
        **base_record,
        "diagnostic_evidence": [["unstaged_work", "clean"]],
        "diagnostic_ownership": f"{fp}:1700000000.0",
        "diagnostic_owner_boot_id": "different-rebooted-boot-id-0000",
        "diagnostic_intervention_required": True,
    }
    parsed = parse_record_payload(payload)
    assert parsed is not None
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_ownership is None
    assert parsed.diagnostic_owner_boot_id is None
    assert parsed.diagnostic_intervention_required is False
    assert next_integration_action(parsed) != IntegrationAction.REQUIRE_INTERVENTION


def test_alive_owner_pid_preserves_hold_at_load_time() -> None:
    import os

    from ralph.pipeline.auto_integrate_record import (
        IntegrationRecord,
        parse_record_payload,
    )
    from ralph.pipeline.operation_evidence import identity_fingerprint

    base_record = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "/tmp/wt",
        "operation_kind": "feature_integrate",
    }
    temp_rec = IntegrationRecord.model_validate(base_record)
    fp = identity_fingerprint(temp_rec)
    lease = f"{fp}:1700000000.0"

    payload = {
        **base_record,
        "diagnostic_evidence": [["unstaged_work", "clean"]],
        "diagnostic_ownership": lease,
        "diagnostic_owner_pid": os.getpid(),
    }
    parsed = parse_record_payload(payload)
    assert parsed is not None
    assert parsed.diagnostic_ownership == lease
    assert parsed.diagnostic_owner_pid == os.getpid()
    assert parsed.diagnostic_evidence == (("unstaged_work", "clean"),)


def test_bump_attempt_clamps_at_cap_and_never_exceeds_cap() -> None:
    from ralph.pipeline._integration_diagnostic import (
        DIAGNOSTIC_BUDGET_SPENT_REASON,
        DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
        _bump_attempt,
    )
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        merge_commit_repair_attempts=DIAGNOSTIC_REPAIR_ATTEMPT_CAP - 1,
    )
    bumped = _bump_attempt(record, "error 1", repair_attempt_cap=DIAGNOSTIC_REPAIR_ATTEMPT_CAP)
    assert bumped.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert bumped.diagnostic_intervention_required is False
    assert bumped.repair_cap_reason == DIAGNOSTIC_BUDGET_SPENT_REASON

    bumped_again = _bump_attempt(
        bumped, "error 2", repair_attempt_cap=DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    )
    assert bumped_again.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert bumped_again.diagnostic_intervention_required is False

    over_cap_record = record.model_copy(update={"merge_commit_repair_attempts": 200})
    bumped_over_cap = _bump_attempt(
        over_cap_record, "error 3", repair_attempt_cap=DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    )
    assert bumped_over_cap.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert bumped_over_cap.diagnostic_intervention_required is False


def test_recovery_on_legacy_work_evidence_missing_lease_auto_releases(tmp_path: Path) -> None:
    import json

    from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        AUTO_INTEGRATE_RECORD_FILENAME,
        read_record,
    )
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": [["work", "modified_file.py"]],
        "diagnostic_ownership": None,
    }
    record_file.write_text(json.dumps(payload), encoding="utf-8")

    recover_incomplete_integration(WorkspaceScope(tmp_path), backend=DEFAULT_FILE_BACKEND)

    loaded = read_record(tmp_path)
    assert loaded is not None
    assert loaded.diagnostic_evidence == ()
    assert loaded.diagnostic_ownership is None
    assert loaded.diagnostic_intervention_required is False
    assert next_integration_action(loaded) != IntegrationAction.REQUIRE_INTERVENTION


def test_recovery_on_malformed_lease_auto_releases(tmp_path: Path) -> None:
    import json

    from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        AUTO_INTEGRATE_RECORD_FILENAME,
        read_record,
    )
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": [["unstaged_work", "clean"]],
        "diagnostic_ownership": "forged_ownership_no_colon",
    }
    record_file.write_text(json.dumps(payload), encoding="utf-8")

    recover_incomplete_integration(WorkspaceScope(tmp_path), backend=DEFAULT_FILE_BACKEND)

    loaded = read_record(tmp_path)
    assert loaded is not None
    assert loaded.diagnostic_evidence == ()
    assert loaded.diagnostic_ownership is None
    assert loaded.diagnostic_intervention_required is False
    assert next_integration_action(loaded) != IntegrationAction.REQUIRE_INTERVENTION


def test_recovery_on_valid_old_lease_differing_fingerprint_preserves_bytes(tmp_path: Path) -> None:
    import time

    from ralph.pipeline._integration_diagnostic import release_verified_diagnostic
    from ralph.pipeline.auto_integrate_record import (
        IntegrationRecord,
    )
    from ralph.pipeline.operation_evidence import EvidenceVerdict

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        owning_worktree="/some/worktree",
        operation_kind="feature_integrate",
        diagnostic_evidence=(("unstaged_work", "clean"),),
        diagnostic_ownership=f"differing_worktree_fingerprint:{time.time()}",
    )
    verdict = release_verified_diagnostic(tmp_path, record)
    assert verdict == EvidenceVerdict.CHANGED
