"""AC-2 / AC-4 fingerprint stability across the record lifecycle (U3 / WU3).

The ``identity_fingerprint`` is the durable identity the diagnostic
lease is bound to. A successful integration MUST NOT change the
fingerprint (AC-4 -- Ralph's own actions, e.g. ``PROMOTE_TO_INTEGRATED``,
``HOLD_DIAGNOSTIC_EVIDENCE``, ``RELEASE_DIAGNOSTIC_EVIDENCE``,
``MARK_RESOLVING_REBASE``, must never count as tampering).

The fingerprint set is the closed trio
``("target", "operation_kind", "owning_worktree")``; a new defaulted
field added to :class:`IntegrationRecord` MUST NOT extend the
fingerprint (AC-2). The test below pins both contracts: a record
whose lifecycle transitions do not change any of the three fields
retains the same fingerprint across the full integration path.
"""

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
    """AC-2: the fingerprint is a closed field set, not a derived dump.

    A new defaulted field added to :class:`IntegrationRecord` cannot
    change the fingerprint because the field is not in
    :data:`_IDENTITY_FINGERPRINT_FIELDS`. The set is structurally
    isolated from record-schema changes.
    """
    assert _IDENTITY_FINGERPRINT_FIELDS == (
        "target",
        "operation_kind",
        "owning_worktree",
    )


def test_fingerprint_is_stable_across_lifecycle_transitions() -> None:
    """AC-4: lifecycle transitions preserve durable identity.

    The diagnostic path is the ONLY writer of the held evidence
    and the lifecycle fields. A successful integration transitions
    ``phase`` to ``"integrated"``, sets ``integrated_feature_sha``,
    clears ``reintegrate_pending``, and may update
    ``pre_target_sha`` -- those transitions describe the record's
    lifecycle, not the durable identity of the operation. The
    fingerprint therefore remains equal across every legal transition.
    """
    record = _seed_record()
    base_fingerprint = identity_fingerprint(record)

    # 1. HOLD_DIAGNOSTIC_EVIDENCE: stores lease and evidence.
    held = record.with_transition(
        HOLD_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(("unstaged_work", "clean"),),
        diagnostic_ownership=f"{base_fingerprint}:1.0",
    )
    assert identity_fingerprint(held) == base_fingerprint

    # 2. RELEASE_DIAGNOSTIC_EVIDENCE: clears held evidence and lease.
    released = held.with_transition(
        RELEASE_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(),
        diagnostic_ownership=None,
        diagnostic_intervention_required=False,
        repair_cap_reason=None,
    )
    assert identity_fingerprint(released) == base_fingerprint

    # 3. MARK_RESOLVING_REBASE: resolver working on paused rebase.
    resolving_rebase = record.with_transition(
        MARK_RESOLVING_REBASE,
        resolving_rebase=True,
        resolving_merge=False,
        pre_target_sha="c" * 40,
    )
    assert identity_fingerprint(resolving_rebase) == base_fingerprint

    # 4. MARK_PENDING_REBASE_CONTINUE: verified stop awaits continuation.
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

    # 5. MARK_RESOLVING_MERGE: resolver working on merge conflicts.
    resolving_merge = record.with_transition(
        MARK_RESOLVING_MERGE,
        resolving_merge=True,
        resolving_paths=("file.py",),
        resolving_stop_sha="s" * 40,
    )
    assert identity_fingerprint(resolving_merge) == base_fingerprint

    # 6. MARK_PENDING_MERGE_COMMIT: resolved merge awaits commit.
    pending_merge = record.with_transition(
        MARK_PENDING_MERGE_COMMIT,
        merge_commit_pending=True,
        merge_commit_head="h" * 40,
        merge_commit_parent="p" * 40,
        merge_commit_tree="t" * 40,
    )
    assert identity_fingerprint(pending_merge) == base_fingerprint

    # 7. ACCEPT_REPAIR_EDITS & RESUME_REPAIR: repair stage transitions.
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

    # 8. BUMP_DIAGNOSTIC_ATTEMPT & RAISE_DIAGNOSTIC_INTERVENTION.
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

    # 9. MARK_LEGACY_FIELDS_PRESERVED.
    legacy_preserved = record.with_transition(
        MARK_LEGACY_FIELDS_PRESERVED,
        diagnostic_evidence=(("unstaged_work", "clean"),),
        diagnostic_ownership="preserved_lease",
        repair_cap_reason=None,
    )
    assert identity_fingerprint(legacy_preserved) == base_fingerprint

    # 10. PROMOTE_TO_INTEGRATED: phase flips, integrated sha set.
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
    """A realistic end-to-end integration lifecycle progression preserves the fingerprint."""
    base = _seed_record()
    base_fp = identity_fingerprint(base)

    # Step 1: Encounter conflict -> mark resolving rebase
    step1 = base.with_transition(
        MARK_RESOLVING_REBASE,
        resolving_rebase=True,
        resolving_merge=False,
        pre_target_sha="c" * 40,
    )
    assert identity_fingerprint(step1) == base_fp

    # Step 2: Diagnostic holds evidence during rebase investigation
    step2 = step1.with_transition(
        HOLD_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(("unstaged_work", "dirty"),),
        diagnostic_ownership=f"{base_fp}:12345.0",
    )
    assert identity_fingerprint(step2) == base_fp

    # Step 3: Diagnostic bumps attempt counter
    step3 = step2.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=1,
        repair_last_error="conflict detected",
    )
    assert identity_fingerprint(step3) == base_fp

    # Step 4: Diagnostic releases evidence when resolved
    step4 = step3.with_transition(
        RELEASE_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(),
        diagnostic_ownership=None,
        diagnostic_intervention_required=False,
        repair_cap_reason=None,
    )
    assert identity_fingerprint(step4) == base_fp

    # Step 5: Mark rebase continue pending
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

    # Step 6: Promote to integrated
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
    """A change in the durable identity (target/operation_kind/owning_worktree) flips the fingerprint."""
    base = _seed_record(target="main", worktree="wt-a")
    base_fp = identity_fingerprint(base)

    # 1. Target variations
    diff_target = base.model_copy(update={"target": "release"})
    assert identity_fingerprint(diff_target) != base_fp

    diff_target_staging = base.model_copy(update={"target": "staging"})
    assert identity_fingerprint(diff_target_staging) != base_fp
    assert identity_fingerprint(diff_target_staging) != identity_fingerprint(diff_target)

    # 2. Owning worktree variations (including None)
    diff_worktree = base.model_copy(update={"owning_worktree": "wt-b"})
    assert identity_fingerprint(diff_worktree) != base_fp

    worktree_none = base.model_copy(update={"owning_worktree": None})
    assert identity_fingerprint(worktree_none) != base_fp
    assert identity_fingerprint(worktree_none) != identity_fingerprint(diff_worktree)

    # 3. Operation kind variations
    diff_op = base.model_copy(update={"operation_kind": "reintegration"})
    assert identity_fingerprint(diff_op) != base_fp

    diff_reconcile = base.model_copy(update={"operation_kind": "target_reconcile"})
    assert identity_fingerprint(diff_reconcile) != base_fp
    assert identity_fingerprint(diff_reconcile) != identity_fingerprint(diff_op)

    # 4. Multiple fields changed together
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
    """AC-2: every non-identity field on IntegrationRecord is excluded from the fingerprint.

    Iterating through every field in ``IntegrationRecord.model_fields``
    that is not in ``_IDENTITY_FINGERPRINT_FIELDS`` demonstrates that
    schema extensions (e.g. adding new defaulted fields) cannot alter
    the identity fingerprint.
    """
    base = _seed_record()
    base_fp = identity_fingerprint(base)

    non_identity_mutations: dict[str, object] = {
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
    }

    # Verify that each field in model_fields (except the closed trio)
    # has a test mutation and that mutating it individually preserves the fingerprint.
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

    # Verify mutating ALL of them simultaneously also preserves the fingerprint.
    all_mutated = base.model_copy(update=non_identity_mutations)
    assert identity_fingerprint(all_mutated) == base_fp


def test_load_time_restore_clears_fingerprint_mismatched_lease() -> None:
    """GAP-5: a held lease whose fingerprint no longer matches is cleared on load."""
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
    """GAP-5 end-to-end: parse_record_payload clears evidence/ownership/intervention on fingerprint mismatch."""
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
    # Durable identity remains intact:
    assert loaded.target == "main"
    assert loaded.owning_worktree == "wt-a"
    assert loaded.operation_kind == "feature_integrate"


def test_parse_record_payload_preserves_matching_lease() -> None:
    """GAP-5: parse_record_payload preserves lease when fingerprint matches."""
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
    """GAP-5 disk read: reading a file with mismatched fingerprint lease clears hold without failing."""
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

    # read_record returns cleared record
    record = read_record(tmp_path, backend=DEFAULT_FILE_BACKEND)
    assert record is not None
    assert record.diagnostic_evidence == ()
    assert record.diagnostic_ownership is None
    assert record.diagnostic_intervention_required is False
    assert record.repair_cap_reason is None

    # read_record_outcome returns PRESENT with cleared record
    outcome = read_record_outcome(tmp_path, backend=DEFAULT_FILE_BACKEND)
    assert outcome.disposition == RecordReadOutcomeDisposition.PRESENT
    assert outcome.record is not None
    assert outcome.record.diagnostic_evidence == ()
    assert outcome.record.diagnostic_ownership is None
    assert outcome.record.diagnostic_intervention_required is False


def test_parse_record_payload_loads_old_record_when_defaulted_field_is_stripped() -> None:
    """AC-2: schema evolution that strips a defaulted field does not invalidate held evidence or ownership.

    When an older on-disk record is missing a field that was added with a default value
    (e.g. ``repair_last_error``), ``parse_record_payload`` must re-populate it with the
    default and leave the held ``diagnostic_evidence`` and ``diagnostic_ownership`` intact.
    """
    import os

    seed = _seed_record(target="main", worktree="wt-a")
    fp = identity_fingerprint(seed)
    # Use a live PID so the owner-is-dead gate inside restamp_fingerprint_mismatch
    # does not clear the evidence before the schema-stripping path is tested.
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
    # Build raw dict in the JSON-compatible form that parse_record_payload expects
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
    # Simulate an old on-disk record that predates the repair_last_error field
    del raw["repair_last_error"]
    loaded = parse_record_payload(raw)
    # (a) reload succeeds
    assert loaded is not None
    # (b) stripped field reverts to its default
    assert loaded.repair_last_error is None
    # (c) held evidence and ownership preserved verbatim
    assert loaded.diagnostic_evidence == seed.diagnostic_evidence
    assert loaded.diagnostic_ownership == seed.diagnostic_ownership
    assert loaded.diagnostic_owner_pid == seed.diagnostic_owner_pid
    assert loaded.diagnostic_owner_boot_id == seed.diagnostic_owner_boot_id
    assert loaded.diagnostic_intervention_required == seed.diagnostic_intervention_required
    # (d) identity fingerprint is stable and lease is still bound to it
    assert identity_fingerprint(loaded) == identity_fingerprint(seed)
    assert loaded.diagnostic_ownership == f"{fp}:1234567.0"


def test_parse_record_payload_drops_unknown_keys_preserving_lease() -> None:
    """Forward-compatibility: unknown keys in the on-disk record are ignored without touching evidence or lease."""
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
    # Inject a key that did not exist when this record was written
    raw["future_field"] = "x"
    loaded = parse_record_payload(raw)
    assert loaded is not None
    assert loaded.diagnostic_intervention_required is False
    assert loaded.diagnostic_evidence == seed.diagnostic_evidence
    assert loaded.diagnostic_ownership == seed.diagnostic_ownership
