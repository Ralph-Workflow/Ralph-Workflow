"""Tests for the git-operation authority's closed classification."""

from __future__ import annotations

import pytest

from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
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
