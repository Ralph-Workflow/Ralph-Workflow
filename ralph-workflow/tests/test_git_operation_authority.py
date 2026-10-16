"""Tests for the git-operation authority's closed classification."""

from __future__ import annotations

import pytest

from ralph.pipeline._git_operation_authority import (
    GitOperationKind,
    classify_git_operation,
    is_in_active_resolution_state,
)
from ralph.pipeline.auto_integrate_record import IntegrationRecord


def _record(**fields: object) -> IntegrationRecord:
    defaults: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
    }
    defaults.update(fields)
    return IntegrationRecord(**defaults)


def test_enum_has_exactly_five_values() -> None:
    assert {member.value for member in GitOperationKind} == {
        "resolving_rebase",
        "resolving_merge",
        "awaiting_continue",
        "awaiting_merge_commit",
        "none",
    }


@pytest.mark.parametrize("resolving_rebase", [False, True])
@pytest.mark.parametrize("resolving_merge", [False, True])
@pytest.mark.parametrize("merge_commit_pending", [False, True])
@pytest.mark.parametrize("rebase_continue_pending", [False, True])
@pytest.mark.parametrize("merge_commit_tree", [None, "c" * 40])
def test_classification_over_all_flag_combinations(
    resolving_rebase: bool,
    resolving_merge: bool,
    merge_commit_pending: bool,
    rebase_continue_pending: bool,
    merge_commit_tree: str | None,
) -> None:
    record = _record(
        resolving_rebase=resolving_rebase,
        resolving_merge=resolving_merge,
        merge_commit_pending=merge_commit_pending,
        rebase_continue_pending=rebase_continue_pending,
        merge_commit_tree=merge_commit_tree,
    )
    kind = classify_git_operation(record)
    if rebase_continue_pending:
        expected = GitOperationKind.AWAITING_CONTINUE
    elif merge_commit_pending:
        expected = GitOperationKind.AWAITING_MERGE_COMMIT
    elif resolving_rebase:
        expected = GitOperationKind.RESOLVING_REBASE
    elif resolving_merge:
        expected = GitOperationKind.RESOLVING_MERGE
    elif merge_commit_tree is not None:
        expected = GitOperationKind.AWAITING_MERGE_COMMIT
    else:
        expected = GitOperationKind.NONE
    assert kind is expected


# ----------------------------------------------------------------------
# is_in_active_resolution_state
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "resolving_merge,resolving_rebase,rebase_continue_pending,merge_commit_pending,merge_commit_tree",
    [
        (True, False, False, False, None),  # RESOLVING_MERGE
        (False, True, False, False, None),  # RESOLVING_REBASE alone
        (False, True, True, False, None),  # RESOLVING_REBASE + AWAITING_CONTINUE overlap
        (True, True, False, False, None),  # both resolving flags
        (True, True, True, False, None),  # both resolving + pending
        (False, False, False, False, None),  # NONE
        (False, False, True, False, None),  # AWAITING_CONTINUE
        (False, False, False, True, None),  # AWAITING_MERGE_COMMIT
        (False, False, False, False, "x"),  # AWAITING_MERGE_COMMIT (tree)
    ],
)
def test_is_in_active_resolution_state(
    resolving_merge: bool,
    resolving_rebase: bool,
    rebase_continue_pending: bool,
    merge_commit_pending: bool,
    merge_commit_tree: str | None,
) -> None:
    """True only for RESOLVING_MERGE or RESOLVING_REBASE (without pending-continue)."""
    record = _record(
        resolving_merge=resolving_merge,
        resolving_rebase=resolving_rebase,
        rebase_continue_pending=rebase_continue_pending,
        merge_commit_pending=merge_commit_pending,
        merge_commit_tree=merge_commit_tree,
    )
    kind = classify_git_operation(record)
    expected = kind in (GitOperationKind.RESOLVING_MERGE, GitOperationKind.RESOLVING_REBASE)
    assert is_in_active_resolution_state(record) is expected


# ----------------------------------------------------------------------
# Caller branch-mapping sanity checks
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "resolving_merge,resolving_rebase,rebase_continue_pending",
    [
        (True, False, False),  # RESOLVING_MERGE → resolution path
        (False, True, False),  # RESOLVING_REBASE → resolution path
        (False, True, True),  # AWAITING_CONTINUE → pending rebase path
        (False, False, True),  # AWAITING_CONTINUE → pending rebase path
        (False, False, False),  # NONE/AWAITING_MERGE_COMMIT → pending merge path
    ],
)
def test_auto_integrate_recovery_branch_mapping(
    resolving_merge: bool,
    resolving_rebase: bool,
    rebase_continue_pending: bool,
) -> None:
    """Branch in auto_integrate_recovery.py maps kind to correct resume path."""
    record = _record(
        resolving_merge=resolving_merge,
        resolving_rebase=resolving_rebase,
        rebase_continue_pending=rebase_continue_pending,
    )
    kind = classify_git_operation(record)
    # Resolution path: RESOLVING_MERGE or RESOLVING_REBASE
    is_resolution_path = kind in (
        GitOperationKind.RESOLVING_MERGE,
        GitOperationKind.RESOLVING_REBASE,
    )
    # Pending rebase path: AWAITING_CONTINUE
    is_pending_rebase = kind is GitOperationKind.AWAITING_CONTINUE
    # Pending merge path: everything else (NONE, AWAITING_MERGE_COMMIT)
    is_pending_merge = kind in (
        GitOperationKind.NONE,
        GitOperationKind.AWAITING_MERGE_COMMIT,
    )
    assert is_resolution_path or is_pending_rebase or is_pending_merge
    # Resolution path is exclusive
    if is_resolution_path:
        assert not is_pending_rebase
        assert not is_pending_merge


@pytest.mark.parametrize(
    "resolving_rebase,rebase_continue_pending",
    [
        (False, True),  # AWAITING_CONTINUE → pending rebase
        (True, False),  # RESOLVING_REBASE → resolution
        (False, False),  # NONE → resolution (no pending)
        (True, True),  # AWAITING_CONTINUE dominates → pending rebase
    ],
)
def test_remote_reconcile_recovery_branch_mapping(
    resolving_rebase: bool,
    rebase_continue_pending: bool,
) -> None:
    """Branch in _remote_reconcile_recovery.py maps AWAITING_CONTINUE to rebase."""
    record = _record(
        resolving_rebase=resolving_rebase,
        rebase_continue_pending=rebase_continue_pending,
    )
    kind = classify_git_operation(record)
    # Original: rebase_continue_pending → resume_pending_rebase else resolution
    expected_rebase = kind is GitOperationKind.AWAITING_CONTINUE
    expected_resolution = not expected_rebase
    assert (kind is GitOperationKind.AWAITING_CONTINUE) is expected_rebase
    assert (kind is not GitOperationKind.AWAITING_CONTINUE) is expected_resolution


# ----------------------------------------------------------------------
# No caller outside the authority module may combine the operation flags
# for control-flow decisions (grep-level assertion).
# ----------------------------------------------------------------------
def test_no_raw_flag_control_flow_in_pipeline() -> None:
    """Grep-level guard: no boolean flag combinations for branching in callers.

    Flag + git-probe combinations (e.g. ``resolving_merge and merge_state``)
    are permitted because the git probe is a necessary runtime verification.
    Pure flag-only combinations are not.
    """
    import re
    from pathlib import Path

    # Patterns that combine operation flags without a git probe.
    # These represent caller-local authority that should live in _git_operation_authority.
    flagged_patterns = [
        r"resolving_merge\s+or\s+resolving_rebase",
        r"resolving_rebase\s+and\s+not\s+rebase_continue_pending",
        r"resolving_merge\s+or\s+\(resolving_rebase\s+and\s+not",
        r"not\s+record\.resolving_merge\b",
        r"record\.rebase_continue_pending\s+and\s+record\.resolving",
    ]

    violations: list[str] = []
    for source in Path("ralph/pipeline/").rglob("*.py"):
        if source.name == "_git_operation_authority.py":
            continue
        # Cheap bytes pre-filter: decode only files mentioning the flags, so
        # the 1s per-test timeout holds under shard load.
        raw = source.read_bytes()
        if b"resolving_merge" not in raw and b"resolving_rebase" not in raw:
            continue
        text = raw.decode(encoding="utf-8")
        for pattern in flagged_patterns:
            for match in re.finditer(pattern, text):
                violations.append(
                    f"{source}:{text[: match.start()].count(chr(10)) + 1}: {match.group(0)!r}"
                )

    assert not violations, (
        f"Raw flag combinations found outside authority:\n{chr(10).join(violations)}"
    )
