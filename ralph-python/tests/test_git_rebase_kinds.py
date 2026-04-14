from __future__ import annotations

from ralph.git.rebase.rebase_kinds import RebaseKind, classify_rebase_error


def test_invalid_revision_is_classified() -> None:
    stderr = "fatal: invalid revision 'feature/unknown'\n"
    result = classify_rebase_error(stderr, "")

    assert result.kind == RebaseKind.INVALID_REVISION
    assert result.metadata.get("revision") == "feature/unknown"


def test_conflicts_include_files() -> None:
    stderr = (
        "CONFLICT (content): Merge conflict in src/app.py\n"
        "CONFLICT (content): Merge conflict in README.md\n"
    )
    result = classify_rebase_error(stderr, "")

    assert result.kind == RebaseKind.CONTENT_CONFLICT
    files = result.metadata.get("files", [])
    assert "src/app.py" in files
    assert "README.md" in files


def test_dirty_worktree_is_detected() -> None:
    stderr = "error: Your local changes to the following files would be overwritten by merge:\n"
    result = classify_rebase_error(stderr, "")

    assert result.kind == RebaseKind.DIRTY_WORKING_TREE


def test_unknown_kind_records_details() -> None:
    stderr = "unexpected failure while rebasing"
    result = classify_rebase_error(stderr, "")

    assert result.kind == RebaseKind.UNKNOWN
    assert "unexpected failure" in result.metadata.get("details", "")
