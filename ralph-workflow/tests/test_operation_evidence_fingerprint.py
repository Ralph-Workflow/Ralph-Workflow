from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline.auto_integrate_record import (
    ACCEPT_REPAIR_EDITS,
    BUMP_DIAGNOSTIC_ATTEMPT,
    HOLD_DIAGNOSTIC_EVIDENCE,
    MARK_LEGACY_FIELDS_PRESERVED,
    MARK_PENDING_MERGE_COMMIT,
    MARK_PENDING_REBASE_CONTINUE,
    MARK_RESOLVING_MERGE,
    MARK_RESOLVING_REBASE,
    PROMOTE_TO_INTEGRATED,
    RAISE_DIAGNOSTIC_INTERVENTION,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    RESUME_REPAIR,
    IntegrationRecord,
    RecordReadOutcomeDisposition,
    parse_record_payload,
    read_record,
    read_record_outcome,
    record_path,
)
from ralph.pipeline.operation_evidence import (
    _IDENTITY_FINGERPRINT_FIELDS,
    identity_fingerprint,
)


def _seed_record(*, target: str = "main", worktree: str = "wt-a") -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        owning_worktree=worktree,
        operation_kind="feature_integrate",
    )


def test_fingerprint_field_set_is_closed_trio() -> None:
    assert _IDENTITY_FINGERPRINT_FIELDS == (
        "target",
        "operation_kind",
        "owning_worktree",
    )


def test_fingerprint_is_stable_across_lifecycle_transitions() -> None:
    record = _seed_record()
    base_fingerprint = identity_fingerprint(record)

    held = record.with_transition(
        HOLD_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(("unstaged_work", "clean"),),
        diagnostic_ownership=f"{base_fingerprint}:1.0",
    )
    assert identity_fingerprint(held) == base_fingerprint

    released = held.with_transition(
        RELEASE_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(),
        diagnostic_ownership=None,
        diagnostic_intervention_required=False,
        repair_cap_reason=None,
    )
    assert identity_fingerprint(released) == base_fingerprint

    resolving_rebase = record.with_transition(
        MARK_RESOLVING_REBASE,
        resolving_rebase=True,
        resolving_merge=False,
        pre_target_sha="c" * 40,
    )
    assert identity_fingerprint(resolving_rebase) == base_fingerprint

    rebase_continue = record.with_transition(
        MARK_PENDING_REBASE_CONTINUE,
        rebase_continue_pending=True,
        rebase_continue_metadata=(("k", "v"),),
        rebase_restore_metadata=None,
        rebase_continue_head="h" * 40,
        rebase_continue_stop="s" * 40,
        rebase_continue_tree="t" * 40,
        rebase_continue_error=None,
    )
    assert identity_fingerprint(rebase_continue) == base_fingerprint

    resolving_merge = record.with_transition(
        MARK_RESOLVING_MERGE,
        resolving_merge=True,
        resolving_paths=("file.py",),
        resolving_stop_sha="s" * 40,
    )
    assert identity_fingerprint(resolving_merge) == base_fingerprint

    pending_merge = record.with_transition(
        MARK_PENDING_MERGE_COMMIT,
        merge_commit_pending=True,
        merge_commit_head="h" * 40,
        merge_commit_parent="p" * 40,
        merge_commit_tree="t" * 40,
    )
    assert identity_fingerprint(pending_merge) == base_fingerprint

    repair_accepted = record.with_transition(
        ACCEPT_REPAIR_EDITS,
        repair_pending_diff="diff content",
        repair_pending_paths=("a.py",),
        repair_commit_controls="author",
        repair_original_tree="orig_tree",
    )
    assert identity_fingerprint(repair_accepted) == base_fingerprint

    repair_resumed = record.with_transition(
        RESUME_REPAIR,
        repair_pending_diff="resumed diff",
        repair_pending_paths=("b.py",),
        repair_commit_controls="author",
    )
    assert identity_fingerprint(repair_resumed) == base_fingerprint

    bumped = record.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=2,
        repair_last_error="transient err",
    )
    assert identity_fingerprint(bumped) == base_fingerprint

    intervened = record.with_transition(
        RAISE_DIAGNOSTIC_INTERVENTION,
        diagnostic_intervention_required=True,
        repair_cap_reason="diagnostic repair exhausted",
    )
    assert identity_fingerprint(intervened) == base_fingerprint

    legacy_preserved = record.with_transition(
        MARK_LEGACY_FIELDS_PRESERVED,
        diagnostic_evidence=(("unstaged_work", "clean"),),
        diagnostic_ownership="preserved_lease",
        repair_cap_reason=None,
    )
    assert identity_fingerprint(legacy_preserved) == base_fingerprint

    promoted = record.with_transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="c" * 40,
        resolving_rebase=False,
        resolving_merge=False,
        rebase_continue_pending=False,
        rebase_continue_metadata=(),
        rebase_restore_metadata=None,
        rebase_continue_head=None,
        rebase_continue_stop=None,
        rebase_continue_tree=None,
        rebase_continue_error=None,
        merge_commit_pending=False,
        reintegrate_pending=False,
    )
    assert promoted.phase == "integrated"
    assert identity_fingerprint(promoted) == base_fingerprint


def test_fingerprint_stable_across_chained_lifecycle_progression() -> None:
    base = _seed_record()
    base_fp = identity_fingerprint(base)

    step1 = base.with_transition(
        MARK_RESOLVING_REBASE,
        resolving_rebase=True,
        resolving_merge=False,
        pre_target_sha="c" * 40,
    )
    assert identity_fingerprint(step1) == base_fp

    step2 = step1.with_transition(
        HOLD_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(("unstaged_work", "dirty"),),
        diagnostic_ownership=f"{base_fp}:12345.0",
    )
    assert identity_fingerprint(step2) == base_fp

    step3 = step2.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=1,
        repair_last_error="conflict detected",
    )
    assert identity_fingerprint(step3) == base_fp

    step4 = step3.with_transition(
        RELEASE_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(),
        diagnostic_ownership=None,
        diagnostic_intervention_required=False,
        repair_cap_reason=None,
    )
    assert identity_fingerprint(step4) == base_fp

    step5 = step4.with_transition(
        MARK_PENDING_REBASE_CONTINUE,
        rebase_continue_pending=True,
        rebase_continue_metadata=(("key", "val"),),
        rebase_restore_metadata=None,
        rebase_continue_head="d" * 40,
        rebase_continue_stop="e" * 40,
        rebase_continue_tree="f" * 40,
        rebase_continue_error=None,
    )
    assert identity_fingerprint(step5) == base_fp

    step6 = step5.with_transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="1" * 40,
        resolving_rebase=False,
        resolving_merge=False,
        rebase_continue_pending=False,
        rebase_continue_metadata=(),
        rebase_restore_metadata=None,
        rebase_continue_head=None,
        rebase_continue_stop=None,
        rebase_continue_tree=None,
        rebase_continue_error=None,
        merge_commit_pending=False,
        reintegrate_pending=False,
    )
    assert identity_fingerprint(step6) == base_fp


def test_fingerprint_changes_only_with_durable_identity() -> None:
    base = _seed_record(target="main", worktree="wt-a")
    base_fp = identity_fingerprint(base)

    diff_target = base.model_copy(update={"target": "release"})
    assert identity_fingerprint(diff_target) != base_fp

    diff_target_staging = base.model_copy(update={"target": "staging"})
    assert identity_fingerprint(diff_target_staging) != base_fp
    assert identity_fingerprint(diff_target_staging) != identity_fingerprint(diff_target)

    diff_worktree = base.model_copy(update={"owning_worktree": "wt-b"})
    assert identity_fingerprint(diff_worktree) != base_fp

    worktree_none = base.model_copy(update={"owning_worktree": None})
    assert identity_fingerprint(worktree_none) != base_fp
    assert identity_fingerprint(worktree_none) != identity_fingerprint(diff_worktree)

    diff_op = base.model_copy(update={"operation_kind": "reintegration"})
    assert identity_fingerprint(diff_op) != base_fp

    diff_reconcile = base.model_copy(update={"operation_kind": "target_reconcile"})
    assert identity_fingerprint(diff_reconcile) != base_fp
    assert identity_fingerprint(diff_reconcile) != identity_fingerprint(diff_op)

    multi_change = base.model_copy(
        update={
            "target": "release",
            "owning_worktree": "wt-z",
            "operation_kind": "target_reconcile",
        }
    )
    assert identity_fingerprint(multi_change) != base_fp
    assert identity_fingerprint(multi_change) not in {
        identity_fingerprint(diff_target),
        identity_fingerprint(diff_worktree),
        identity_fingerprint(diff_reconcile),
    }


def test_fingerprint_schema_immunity_and_defaulted_fields() -> None:
    base = _seed_record()
    base_fp = identity_fingerprint(base)

    non_identity_mutations: dict[str, object] = {
        "record_version": 99,
        "phase": "integrated",
        "pre_feature_sha": "x" * 40,
        "pre_target_sha": "y" * 40,
        "integrated_feature_sha": "z" * 40,
        "resolving_rebase": True,
        "rebase_continue_pending": True,
        "rebase_continue_metadata": (("meta", "data"),),
        "rebase_restore_metadata": (("meta", "data"),),
        "rebase_continue_head": "head_sha",
        "rebase_continue_stop": "stop_sha",
        "rebase_continue_tree": "tree_sha",
        "rebase_continue_error": "err",
        "resolving_merge": True,
        "resolving_paths": ("path1", "path2"),
        "resolving_stop_sha": "stop_sha",
        "merge_commit_pending": True,
        "merge_commit_repair_attempts": 4,
        "repair_original_tree": "orig_tree",
        "repair_last_error": "last_err",
        "diagnostic_evidence": (("unstaged_work", "clean"),),
        "diagnostic_ownership": "lease:123.0",
        "diagnostic_owner_pid": 12345,
        "diagnostic_owner_boot_id": "test-boot-id",
        "diagnostic_intervention_required": True,
        "repair_cap_reason": "exhausted",
        "repair_pending_diff": "diff",
        "repair_pending_paths": ("p",),
        "repair_commit_controls": "controls",
        "merge_commit_head": "m_head",
        "merge_commit_parent": "m_parent",
        "merge_commit_tree": "m_tree",
        "reintegrate_pending": True,
        "initiating_feature_sha": "init_sha",
        "resolution_gate_key": "gate:stop1:primary:sha",
        "waiting_notification_key": "gate:stop1:primary:sha",
        "resolution_terminal_marker": (
            ("stop1", ("primary",), "sha"),
            "NeedsOperator",
            "ralph --resume",
        ),
    }

    for field_name in IntegrationRecord.model_fields:
        if field_name in _IDENTITY_FINGERPRINT_FIELDS or field_name == "last_failure_cause":
            continue
        assert field_name in non_identity_mutations, (
            f"Missing test mutation for field: {field_name}"
        )
        mutated = base.model_copy(update={field_name: non_identity_mutations[field_name]})
        assert identity_fingerprint(mutated) == base_fp, (
            f"Field {field_name} leaked into fingerprint!"
        )

    all_mutated = base.model_copy(update=non_identity_mutations)
    assert identity_fingerprint(all_mutated) == base_fp


def test_load_time_restore_clears_fingerprint_mismatched_lease() -> None:
    from ralph.pipeline._auto_integrate_record_restamp import (
        restamp_fingerprint_mismatch,
    )

    record = _seed_record(target="main")
    record = record.model_copy(
        update={
            "diagnostic_evidence": (("unstaged_work", "dirty"),),
            "diagnostic_ownership": "stale-fingerprint:1.0",
            "diagnostic_intervention_required": True,
            "repair_cap_reason": "diagnostic repair exhausted; operator intervention required",
        }
    )
    cleared = restamp_fingerprint_mismatch(record, with_cause=record.model_dump())
    assert cleared.diagnostic_evidence == ()
    assert cleared.diagnostic_ownership is None
    assert cleared.diagnostic_intervention_required is False
    assert cleared.repair_cap_reason is None


def test_parse_record_payload_clears_fingerprint_mismatched_lease() -> None:
    raw_payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "wt-a",
        "operation_kind": "feature_integrate",
        "diagnostic_evidence": [["unstaged_work", "dirty"]],
        "diagnostic_ownership": "stale_fingerprint_from_v1:999999.0",
        "diagnostic_intervention_required": True,
        "repair_cap_reason": "diagnostic repair exhausted; operator intervention required",
    }
    loaded = parse_record_payload(raw_payload)
    assert loaded is not None
    assert loaded.diagnostic_evidence == ()
    assert loaded.diagnostic_ownership is None
    assert loaded.diagnostic_intervention_required is False
    assert loaded.repair_cap_reason is None
    assert loaded.target == "main"
    assert loaded.owning_worktree == "wt-a"
    assert loaded.operation_kind == "feature_integrate"


def test_parse_record_payload_preserves_matching_lease() -> None:
    record = _seed_record(target="main", worktree="wt-a")
    fp = identity_fingerprint(record)
    valid_lease = f"{fp}:1234567.0"

    raw_payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "wt-a",
        "operation_kind": "feature_integrate",
        "diagnostic_evidence": [["unstaged_work", "clean"]],
        "diagnostic_ownership": valid_lease,
        "diagnostic_intervention_required": False,
    }
    loaded = parse_record_payload(raw_payload)
    assert loaded is not None
    assert loaded.diagnostic_evidence == (("unstaged_work", "clean"),)
    assert loaded.diagnostic_ownership == valid_lease
    assert loaded.diagnostic_intervention_required is False


def test_read_record_outcome_clears_fingerprint_mismatched_lease(tmp_path: Path) -> None:
    raw_payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "wt-a",
        "operation_kind": "feature_integrate",
        "diagnostic_evidence": [["unstaged_work", "dirty"]],
        "diagnostic_ownership": "mismatched_fingerprint_on_disk:12345.0",
        "diagnostic_intervention_required": True,
        "repair_cap_reason": "diagnostic repair exhausted; operator intervention required",
    }
    rec_file = record_path(tmp_path)
    rec_file.parent.mkdir(parents=True, exist_ok=True)
    rec_file.write_text(json.dumps(raw_payload), encoding="utf-8")

    record = read_record(tmp_path, backend=DEFAULT_FILE_BACKEND)
    assert record is not None
    assert record.diagnostic_evidence == ()
    assert record.diagnostic_ownership is None
    assert record.diagnostic_intervention_required is False
    assert record.repair_cap_reason is None

    outcome = read_record_outcome(tmp_path, backend=DEFAULT_FILE_BACKEND)
    assert outcome.disposition == RecordReadOutcomeDisposition.PRESENT
    assert outcome.record is not None
    assert outcome.record.diagnostic_evidence == ()
    assert outcome.record.diagnostic_ownership is None
    assert outcome.record.diagnostic_intervention_required is False


def test_parse_record_payload_loads_old_record_when_defaulted_field_is_stripped() -> None:
    import os

    seed = _seed_record(target="main", worktree="wt-a")
    fp = identity_fingerprint(seed)
    live_pid = os.getpid()
    seed = seed.model_copy(
        update={
            "diagnostic_evidence": (("unstaged_work", "clean"),),
            "diagnostic_ownership": f"{fp}:1234567.0",
            "diagnostic_owner_pid": live_pid,
            "diagnostic_owner_boot_id": None,
            "diagnostic_intervention_required": False,
            "repair_cap_reason": None,
        }
    )
    raw: dict[str, object] = {
        "phase": seed.phase,
        "target": seed.target,
        "pre_feature_sha": seed.pre_feature_sha,
        "pre_target_sha": seed.pre_target_sha,
        "owning_worktree": seed.owning_worktree,
        "operation_kind": seed.operation_kind,
        "diagnostic_evidence": [["unstaged_work", "clean"]],
        "diagnostic_ownership": f"{fp}:1234567.0",
        "diagnostic_owner_pid": live_pid,
        "diagnostic_owner_boot_id": None,
        "diagnostic_intervention_required": False,
        "repair_cap_reason": None,
        "repair_last_error": None,
    }
    del raw["repair_last_error"]
    loaded = parse_record_payload(raw)
    assert loaded is not None
    assert loaded.repair_last_error is None
    assert loaded.diagnostic_evidence == seed.diagnostic_evidence
    assert loaded.diagnostic_ownership == seed.diagnostic_ownership
    assert loaded.diagnostic_owner_pid == seed.diagnostic_owner_pid
    assert loaded.diagnostic_owner_boot_id == seed.diagnostic_owner_boot_id
    assert loaded.diagnostic_intervention_required == seed.diagnostic_intervention_required
    assert identity_fingerprint(loaded) == identity_fingerprint(seed)
    assert loaded.diagnostic_ownership == f"{fp}:1234567.0"


def test_parse_record_payload_drops_unknown_keys_preserving_lease() -> None:
    seed = _seed_record(target="main", worktree="wt-a")
    fp = identity_fingerprint(seed)
    valid_lease = f"{fp}:1234567.0"
    seed = seed.model_copy(
        update={
            "diagnostic_evidence": (("unstaged_work", "clean"),),
            "diagnostic_ownership": valid_lease,
            "diagnostic_intervention_required": False,
        }
    )
    raw = seed.model_dump()
    raw["future_field"] = "x"
    loaded = parse_record_payload(raw)
    assert loaded is not None
    assert loaded.diagnostic_intervention_required is False
    assert loaded.diagnostic_evidence == seed.diagnostic_evidence
    assert loaded.diagnostic_ownership == seed.diagnostic_ownership
