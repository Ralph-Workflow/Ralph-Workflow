"""Unit tests for CommitReview artifact model, normalizer, and citation verifier."""

from __future__ import annotations

from typing import NamedTuple

import pytest
from pydantic import ValidationError

from ralph.mcp.artifacts._commit_review import CommitReview
from ralph.mcp.artifacts._commit_review_action import CommitReviewAction
from ralph.mcp.artifacts._typed_artifact_validation_error import TypedArtifactValidationError
from ralph.mcp.artifacts.citation_verifier import verify_reject_citations
from ralph.mcp.artifacts.typed_artifacts import normalize_commit_review_content

_VALID_TREE_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_valid_cleanup_approve_artifact() -> None:
    """Test that verdict=approve with tree_sha and empty actions is valid."""
    result = normalize_commit_review_content(
        {"verdict": "approve", "tree_sha": _VALID_TREE_SHA, "actions": []}
    )
    assert result == {
        "verdict": "approve",
        "tree_sha": _VALID_TREE_SHA,
        "actions": [],
    }


def test_valid_cleanup_with_delete_action() -> None:
    """Test that delete_file action is valid."""
    result = normalize_commit_review_content(
        {
            "verdict": "approve",
            "tree_sha": _VALID_TREE_SHA,
            "actions": [{"action": "delete_file", "path": "build/out.exe"}],
        }
    )
    assert result == {
        "verdict": "approve",
        "tree_sha": _VALID_TREE_SHA,
        "actions": [{"action": "delete_file", "path": "build/out.exe"}],
    }


def test_valid_cleanup_with_gitignore_action() -> None:
    """Test that add_to_gitignore action is valid."""
    result = normalize_commit_review_content(
        {
            "verdict": "approve",
            "tree_sha": _VALID_TREE_SHA,
            "actions": [{"action": "add_to_gitignore", "pattern": "*.exe"}],
        }
    )
    assert result == {
        "verdict": "approve",
        "tree_sha": _VALID_TREE_SHA,
        "actions": [{"action": "add_to_gitignore", "pattern": "*.exe"}],
    }


def test_valid_cleanup_with_git_exclude_action() -> None:
    """Test that add_to_git_exclude action is valid."""
    result = normalize_commit_review_content(
        {
            "verdict": "approve",
            "tree_sha": _VALID_TREE_SHA,
            "actions": [{"action": "add_to_git_exclude", "pattern": ".env.local"}],
        }
    )
    assert result == {
        "verdict": "approve",
        "tree_sha": _VALID_TREE_SHA,
        "actions": [{"action": "add_to_git_exclude", "pattern": ".env.local"}],
    }


def test_invalid_action_type_rejected() -> None:
    """Test that unknown action types are rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {
                "verdict": "approve",
                "tree_sha": _VALID_TREE_SHA,
                "actions": [{"action": "rename_file", "path": "x"}],
            }
        )


def test_delete_file_action_requires_path() -> None:
    """Test that delete_file action requires a path field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {
                "verdict": "approve",
                "tree_sha": _VALID_TREE_SHA,
                "actions": [{"action": "delete_file"}],
            }
        )


def test_gitignore_action_requires_pattern() -> None:
    """Test that add_to_gitignore action requires a pattern field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {
                "verdict": "approve",
                "tree_sha": _VALID_TREE_SHA,
                "actions": [{"action": "add_to_gitignore"}],
            }
        )


def test_extra_fields_forbidden() -> None:
    """Test that extra fields beyond the schema are rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {
                "verdict": "approve",
                "tree_sha": _VALID_TREE_SHA,
                "actions": [],
                "unknown_field": "x",
            }
        )


def test_verdict_required() -> None:
    """Test that verdict field is required."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content({"tree_sha": _VALID_TREE_SHA, "actions": []})


def test_tree_sha_required() -> None:
    """Test that tree_sha field is required."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content({"verdict": "approve", "actions": []})


def test_invalid_tree_sha_rejected() -> None:
    """Test that non-40-hex tree_sha is rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {"verdict": "approve", "tree_sha": "not_a_valid_sha", "actions": []}
        )


def test_invalid_verdict_rejected() -> None:
    """Test that invalid verdict is rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {"verdict": "maybe", "tree_sha": _VALID_TREE_SHA, "actions": []}
        )


def test_reject_verdict_requires_reason() -> None:
    """Test that verdict=reject requires a non-empty reason."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {"verdict": "reject", "tree_sha": _VALID_TREE_SHA, "actions": []}
        )

    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {"verdict": "reject", "tree_sha": _VALID_TREE_SHA, "actions": [], "reason": "   "}
        )


def test_reject_verdict_with_reason_valid() -> None:
    """Test that verdict=reject with reason succeeds."""
    result = normalize_commit_review_content(
        {
            "verdict": "reject",
            "tree_sha": _VALID_TREE_SHA,
            "actions": [],
            "reason": "Mass deletion of source files",
        }
    )
    assert result["verdict"] == "reject"
    assert result["reason"] == "Mass deletion of source files"


def test_legacy_analysis_complete_rejected() -> None:
    """Test that legacy analysis_complete field without verdict/tree_sha is rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content({"analysis_complete": True, "actions": []})


def test_legacy_analysis_complete_is_not_an_accepted_extra_field() -> None:
    """The retired ``analysis_complete`` field is rejected even beside a valid verdict.

    The contract has a single verdict field. ``analysis_complete`` is not a
    second spelling of it, so the closed schema must refuse the payload
    instead of quietly ignoring the legacy key.
    """
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {
                "verdict": "approve",
                "tree_sha": _VALID_TREE_SHA,
                "actions": [],
                "analysis_complete": True,
            }
        )


def test_git_exclude_action_requires_pattern() -> None:
    """Test that add_to_git_exclude action requires a pattern field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_review_content(
            {
                "verdict": "approve",
                "tree_sha": _VALID_TREE_SHA,
                "actions": [{"action": "add_to_git_exclude"}],
            }
        )


def test_multiple_actions_valid() -> None:
    """Test that multiple actions are valid."""
    result = normalize_commit_review_content(
        {
            "verdict": "approve",
            "tree_sha": _VALID_TREE_SHA,
            "actions": [
                {"action": "delete_file", "path": "build/out.exe"},
                {"action": "add_to_gitignore", "pattern": "*.pyc"},
                {"action": "add_to_git_exclude", "pattern": ".env.local"},
            ],
        }
    )
    assert result == {
        "verdict": "approve",
        "tree_sha": _VALID_TREE_SHA,
        "actions": [
            {"action": "delete_file", "path": "build/out.exe"},
            {"action": "add_to_gitignore", "pattern": "*.pyc"},
            {"action": "add_to_git_exclude", "pattern": ".env.local"},
        ],
    }


def test_reason_is_optional_on_approve() -> None:
    """Test that the optional reason field is accepted on approve."""
    result = normalize_commit_review_content(
        {
            "verdict": "approve",
            "tree_sha": _VALID_TREE_SHA,
            "actions": [],
            "reason": "All cleanup complete",
        }
    )
    assert result["reason"] == "All cleanup complete"


def test_delete_file_path_rejects_newline() -> None:
    """A newline in ``path`` must be rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        CommitReviewAction(action="delete_file", path="foo\nbar")


def test_add_to_gitignore_pattern_rejects_tab() -> None:
    """A tab character in ``pattern`` must be rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        CommitReviewAction(action="add_to_gitignore", pattern="*.py\t")


def test_add_to_gitignore_pattern_rejects_null_byte() -> None:
    """A null byte in ``pattern`` must be rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        CommitReviewAction(action="add_to_gitignore", pattern="*.\x00py")


def test_delete_file_path_rejects_comment_injection() -> None:
    """A ``#``-prefixed ``path`` must be rejected."""
    with pytest.raises(ValidationError):
        CommitReviewAction(action="delete_file", path="#comment-injection")


def test_add_to_gitignore_pattern_rejects_comment_prefix() -> None:
    """A ``#``-prefixed ``pattern`` must be rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        CommitReviewAction(action="add_to_gitignore", pattern="#*.pyc")


def test_delete_file_path_rejects_whitespace_only() -> None:
    """A whitespace-only ``path`` must be rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        CommitReviewAction(action="delete_file", path="   ")


@pytest.mark.parametrize(
    "action, kwargs",
    [
        ("delete_file", {"path": ".agent/raw/opencode.log"}),
        ("delete_file", {"path": "checkpoint.json"}),
        ("delete_file", {"path": "tmp/scratch.txt"}),
        ("add_to_gitignore", {"pattern": "*.pyc"}),
        ("add_to_gitignore", {"pattern": ".agent/"}),
        ("add_to_git_exclude", {"pattern": ".env.local"}),
        ("add_to_git_exclude", {"pattern": "/checkpoint.json"}),
    ],
)
def test_valid_paths_still_accepted(action: str, kwargs: dict[str, str]) -> None:
    """Normal, non-malicious paths must still validate after the tightening."""
    if action == "delete_file":
        obj = CommitReviewAction(action=action, path=kwargs["path"])
        assert obj.action == action
        assert obj.path == kwargs["path"]
    else:
        obj = CommitReviewAction(action=action, pattern=kwargs["pattern"])
        assert obj.action == action
        assert obj.pattern == kwargs["pattern"]


@pytest.mark.parametrize(
    ("boundary_char", "expected_accepted"),
    [
        ("\x20", False),
        ("\x21", True),
        ("\x7e", True),
        ("\x7f", False),
        ("\x80", False),
        ("\xff", False),
    ],
    ids=[
        "space-0x20",
        "ascii-start-0x21",
        "ascii-end-0x7e",
        "del-0x7f",
        "non-ascii-low-0x80",
        "non-ascii-high-0xff",
    ],
)
def test_commit_review_action_rejects_printable_ascii_boundaries(
    boundary_char: str, expected_accepted: bool
) -> None:
    """Boundary chars in the printable-ASCII regex are correctly accepted/rejected."""
    for action_type, kwarg in (
        ("delete_file", "path"),
        ("add_to_gitignore", "pattern"),
        ("add_to_git_exclude", "pattern"),
    ):
        kwargs: dict[str, str] = {"action": action_type, kwarg: boundary_char}
        if expected_accepted:
            obj = CommitReviewAction(**kwargs)
            val: object = getattr(obj, kwarg)
            assert val == boundary_char
        else:
            with pytest.raises(ValidationError):
                CommitReviewAction(**kwargs)


def test_commit_review_action_model_validator_passes_when_both_path_and_pattern_set() -> None:
    """The model_validator does not check that path/pattern are mutually exclusive."""
    obj = CommitReviewAction(action="delete_file", path="foo", pattern="bar")
    assert obj.action == "delete_file"
    assert obj.path == "foo"
    assert obj.pattern == "bar"

    obj2 = CommitReviewAction(action="add_to_gitignore", path="extra", pattern="*.pyc")
    assert obj2.action == "add_to_gitignore"
    assert obj2.path == "extra"
    assert obj2.pattern == "*.pyc"


def test_commit_review_reason_is_currently_unvalidated() -> None:
    """The ``reason`` field on ``CommitReview`` accepts unvalidated text when approve."""
    for raw in ("\x00nul-injection", "中文说明", "multi\nline\nreason", "\r\nwindows-newline"):
        artifact = CommitReview(
            verdict="approve",
            tree_sha=_VALID_TREE_SHA,
            actions=[],
            reason=raw,
        )
        assert artifact.reason == raw, (
            f"reason={raw!r} must be accepted unchanged (no field_validator), "
            f"got: {artifact.reason!r}"
        )


def test_commit_review_action_accepts_none_path_and_pattern() -> None:
    """``path`` and ``pattern`` accept ``None`` -- the field_validators short-circuit."""
    obj = CommitReviewAction(action="delete_file", path="foo", pattern=None)
    assert obj.path == "foo"
    assert obj.pattern is None

    obj2 = CommitReviewAction(action="add_to_gitignore", path=None, pattern="*.pyc")
    assert obj2.path is None
    assert obj2.pattern == "*.pyc"


def test_commit_review_action_rejects_empty_string_path() -> None:
    """Empty string fails the printable-ASCII regex (no one-or-more match)."""
    with pytest.raises(ValidationError):
        CommitReviewAction(action="delete_file", path="")
    with pytest.raises(ValidationError):
        CommitReviewAction(action="add_to_gitignore", pattern="")


# ---------------------------------------------------------------------------
# Citation verifier tests
# ---------------------------------------------------------------------------


class _FakeDiffEntry(NamedTuple):
    path: str
    status: str = "M"
    is_binary: bool = False
    size: int = 100
    base_tree_paths: frozenset[str] | None = None


def test_citation_verifier_valid_path_match() -> None:
    """Valid path in reason matches diff entry."""
    diff: list[object] = [_FakeDiffEntry(path="src/main.py", status="M")]
    assert verify_reject_citations("Accidental modification of src/main.py", diff) is True


def test_citation_verifier_unmatched_path_rejected() -> None:
    """Unmatched path in reason fails verification."""
    diff: list[object] = [_FakeDiffEntry(path="src/main.py", status="M")]
    assert verify_reject_citations("Accidental deletion of src/other.py", diff) is False


def test_citation_verifier_absent_tracked_file_citation_is_rejected() -> None:
    """A cited tracked file that is absent from the diff fails verification.

    The cited name carries no file extension, so it is recognized as a path
    only because the base tree tracks it.  Ignoring such a claim silently
    would let a fabricated reason authorize the rollback path.
    """
    diff: list[object] = [
        _FakeDiffEntry(
            path="src/main.py",
            status="M",
            base_tree_paths=frozenset({"src/main.py", "Makefile"}),
        )
    ]
    assert (
        verify_reject_citations(
            "src/main.py was modified and Makefile was deleted",
            diff,
        )
        is False
    )
    assert verify_reject_citations("src/main.py was modified", diff) is True


def test_citation_verifier_multiple_paths_all_must_match() -> None:
    """All cited paths must be in the diff."""
    diff: list[object] = [
        _FakeDiffEntry(path="src/a.py", status="D"),
        _FakeDiffEntry(path="src/b.py", status="D"),
    ]
    # Both match
    assert (
        verify_reject_citations(
            ["- [R1] Deleted src/a.py", "- [R2] Deleted src/b.py"],
            diff,
        )
        is True
    )

    # One matches, one hallucinated
    assert (
        verify_reject_citations(
            ["- [R1] Deleted src/a.py", "- [R2] Deleted src/c.py"],
            diff,
        )
        is False
    )


def test_citation_verifier_basename_only_citation_is_rejected() -> None:
    """A bare basename does not verify a nested path in the diff.

    Citations are matched as exact repository-relative paths, so naming
    ``runner.py`` never cites the changed ``ralph-workflow/ralph/pipeline/
    runner.py``.  A basename-only citation is unverifiable evidence and must
    fail closed.
    """
    diff: list[object] = [
        _FakeDiffEntry(path="ralph-workflow/ralph/pipeline/runner.py", status="M")
    ]
    assert verify_reject_citations("Issue in `runner.py` detected", diff) is False


def test_citation_verifier_count_match() -> None:
    """Count of deleted files matches diff count."""
    diff: list[object] = [
        _FakeDiffEntry(path="a.txt", status="D"),
        _FakeDiffEntry(path="b.txt", status="D"),
        _FakeDiffEntry(path="c.txt", status="M"),
    ]
    assert verify_reject_citations("Mass deletion: 2 deleted files", diff) is True


def test_citation_verifier_mismatched_count_rejected() -> None:
    """Count mismatch fails verification."""
    diff: list[object] = [
        _FakeDiffEntry(path="a.txt", status="D"),
    ]
    assert verify_reject_citations("Mass deletion of 10 files", diff) is False


def test_citation_verifier_empty_reason_or_diff() -> None:
    """Empty reason or empty diff fails verification."""
    diff: list[object] = [_FakeDiffEntry(path="a.txt", status="D")]
    assert verify_reject_citations("", diff) is False
    assert verify_reject_citations([], diff) is False
    assert verify_reject_citations("Deleted a.txt", []) is False


def test_citation_verifier_no_citations_rejected() -> None:
    """Reason with no path or count citations fails verification."""
    diff: list[object] = [_FakeDiffEntry(path="a.txt", status="D")]
    assert verify_reject_citations("I do not like these changes", diff) is False


def test_citation_verifier_absent_reference_doc_citation_is_rejected() -> None:
    """A cited path absent from the diff fails verification.

    Reference documents such as ``AGENTS.md`` are not exempt: every cited
    path must be present in the proposal diff.  A reason that names a path
    the diff does not contain is unverifiable evidence and must fail closed.
    """
    diff: list[object] = [_FakeDiffEntry(path="src/config.py", status="M")]
    assert (
        verify_reject_citations(
            "AGENTS.md policy violated by changes in `src/config.py`",
            diff,
        )
        is False
    )


def test_citation_verifier_fabricated_reject_citations_fail_closed() -> None:
    """Every fabricated reject citation fails closed against the real diff.

    The proposal diff holds a single modified, non-binary ``src/main.py``.
    Each claim below invents evidence the diff does not contain, and each one
    used to pass verification, which authorized the destructive rollback path
    on a reviewer malfunction.
    """
    diff: list[object] = [_FakeDiffEntry(path="src/main.py", status="M")]

    # A binary count with no binary entry behind it.
    assert verify_reject_citations("src/main.py and 999 binary files were damaged", diff) is False
    # A cited path that the diff does not contain.
    assert verify_reject_citations("src/main.py and README.md were deleted", diff) is False
    # A path that only ends with a diff path.
    assert verify_reject_citations("outside/src/main.py was deleted", diff) is False
    # An added-file count with no added entry behind it.
    assert verify_reject_citations("src/main.py and 1 added file were damaged", diff) is False


def test_citation_verifier_valid_citation_still_verifies() -> None:
    """A citation that matches the diff exactly still verifies."""
    diff: list[object] = [_FakeDiffEntry(path="src/main.py", status="M")]
    assert verify_reject_citations("Accidental modification of src/main.py", diff) is True


def test_citation_verifier_counts_are_typed_against_the_diff() -> None:
    """A count is backed only by entries of the category it names.

    The diff holds two modified entries (``src/main.py`` and the binary
    ``assets/logo.bin``), one added entry, no deleted entry, and one binary
    entry.
    """
    diff: list[object] = [
        _FakeDiffEntry(path="src/main.py", status="M"),
        _FakeDiffEntry(path="src/new.py", status="A"),
        _FakeDiffEntry(path="assets/logo.bin", status="M", is_binary=True),
    ]

    assert verify_reject_citations("2 modified files were corrupted", diff) is True
    assert verify_reject_citations("1 modified file was corrupted", diff) is False
    assert verify_reject_citations("1 added file was added", diff) is True
    assert verify_reject_citations("2 added files were added", diff) is False
    assert verify_reject_citations("1 binary file was corrupted", diff) is True
    assert verify_reject_citations("2 binary files were corrupted", diff) is False
    # A count whose category has no entry at all never verifies.
    assert verify_reject_citations("1 deleted file was removed", diff) is False


def test_citation_verifier_one_false_claim_fails_the_whole_reason() -> None:
    """Every cited path and count of a reason must verify."""
    diff: list[object] = [
        _FakeDiffEntry(path="src/a.py", status="D"),
        _FakeDiffEntry(path="src/b.py", status="D"),
    ]

    assert (
        verify_reject_citations(
            ["- [R1] Deleted src/a.py", "- [R2] Deleted src/b.py"],
            diff,
        )
        is True
    )
    # The path claim is true, the count claim is not: the reason fails.
    assert (
        verify_reject_citations(
            ["- [R1] Deleted src/a.py", "- [R2] 3 deleted files were removed"],
            diff,
        )
        is False
    )


def test_citation_verifier_paths_match_the_exact_repository_relative_path() -> None:
    """A path matches only its exact normalized repository-relative form."""
    diff: list[object] = [_FakeDiffEntry(path="src/main.py", status="M")]

    assert verify_reject_citations("Changed `src/main.py`", diff) is True
    assert verify_reject_citations("Changed `./src/main.py`", diff) is True
    assert verify_reject_citations("Changed **src/main.py**", diff) is True
    assert verify_reject_citations('Changed "src/main.py"', diff) is True
    assert verify_reject_citations("Changed src/main.py:42", diff) is True
    # Suffix, prefix, and substring matches are not citations of this path.
    assert verify_reject_citations("Changed src/main.py.bak", diff) is False
    assert verify_reject_citations("Changed other/src/main.py", diff) is False
    assert verify_reject_citations("Changed main.py", diff) is False


#: Reject reasons whose count claim is false for a proposal diff that holds
#: exactly ONE deleted ``src/main.py`` entry.  Every reason names a real
#: path and a fabricated quantity, and every quantity is written with a
#: noun a reviewer may plausibly use for a diff count.
_FABRICATED_COUNT_REASONS = (
    "Deleted src/main.py and 999 paths",
    "Deleted src/main.py; 999 deletions",
    "Deleted src/main.py and 999 entries",
    "Deleted src/main.py, 999 changes",
    "Deleted src/main.py, 999 lines",
    "Deleted src/main.py, 999 items",
    "Deleted src/main.py, 999 paths deleted",
    "Deleted src/main.py and 999 modifications",
    "Deleted src/main.py; deleted files: 999",
    "Deleted src/main.py; nine deleted files",
)


@pytest.mark.parametrize("reason", _FABRICATED_COUNT_REASONS)
def test_citation_verifier_fabricated_count_nouns_fail_closed(reason: str) -> None:
    """A fabricated count never verifies, whatever noun the reviewer writes.

    The proposal diff holds exactly one deleted ``src/main.py`` entry, so
    every count above is false.  A count the verifier cannot recognise is a
    count it cannot check, and an unchecked count must never authorise the
    destructive rollback path.
    """
    diff: list[object] = [_FakeDiffEntry(path="src/main.py", status="D")]

    assert verify_reject_citations(reason, diff) is False


def test_citation_verifier_accepted_count_forms_still_verify() -> None:
    """The accepted count syntax still verifies when the diff backs it.

    The tightening recognises more counts, so it must keep recognising the
    counts it always recognised: a plain ``N files`` claim, an explicit
    ``count: N`` claim, and a typed ``N deleted files`` claim.
    """
    diff: list[object] = [
        _FakeDiffEntry(path=f"src/file_{index}.py", status="D") for index in range(999)
    ]

    assert verify_reject_citations("999 files", diff) is True
    assert verify_reject_citations("count: 999", diff) is True
    assert verify_reject_citations("999 deleted files", diff) is True

    modified_diff: list[object] = [
        _FakeDiffEntry(path="src/main.py", status="M"),
        _FakeDiffEntry(path="src/other.py", status="M"),
    ]
    assert verify_reject_citations("2 modified files were corrupted", modified_diff) is True


def test_citation_verifier_count_nouns_the_diff_cannot_back_fail_closed() -> None:
    """A count the diff cannot back is unverifiable, so it fails closed.

    The diff summary carries paths and statuses, never line counts: a claim
    about lines has no backing fact, so it can never authorise a rollback.
    """
    diff: list[object] = [_FakeDiffEntry(path="src/main.py", status="D")]

    assert verify_reject_citations("Deleted src/main.py and 1 line", diff) is False


def test_citation_verifier_non_count_numbers_are_not_count_claims() -> None:
    """A number that names no diff count never becomes a count claim.

    A path that carries digits, a path with a line suffix, and a bare line
    number are references to a place in the tree, not quantities of the
    diff: reading them as counts would make a legitimate reason fail closed.
    """
    diff: list[object] = [_FakeDiffEntry(path="src/2024/report.py", status="M")]

    assert verify_reject_citations("Changed src/2024/report.py", diff) is True
    assert verify_reject_citations("Changed src/2024/report.py:42", diff) is True
    assert verify_reject_citations("Changed src/2024/report.py at line 42", diff) is True
    assert verify_reject_citations("Line 42 of src/2024/report.py is broken", diff) is True
    assert verify_reject_citations("Line 42 of the file src/2024/report.py is broken", diff) is True


def test_citation_verifier_reversed_count_order_and_spelled_out_fail_closed() -> None:
    """Reversed count order and spelled-out quantities fail closed when unverifiable."""
    diff: list[object] = [_FakeDiffEntry(path="src/app.py", status="M")]
    assert verify_reject_citations("src/app.py modified; deleted files: 999", diff) is False
    assert verify_reject_citations("src/app.py modified; nine deleted files", diff) is False
