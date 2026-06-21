"""Unit tests for CommitCleanup artifact model and normalizer."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ralph.mcp.artifacts._commit_cleanup import CommitCleanup
from ralph.mcp.artifacts._commit_cleanup_action import CommitCleanupAction
from ralph.mcp.artifacts._typed_artifact_validation_error import TypedArtifactValidationError
from ralph.mcp.artifacts.typed_artifacts import normalize_commit_cleanup_content


def test_valid_cleanup_done_artifact() -> None:
    """Test that analysis_complete=True with empty actions is valid."""
    result = normalize_commit_cleanup_content({"analysis_complete": True, "actions": []})
    assert result["analysis_complete"] is True
    assert result["actions"] == []


def test_valid_cleanup_with_delete_action() -> None:
    """Test that delete_file action is valid."""
    result = normalize_commit_cleanup_content(
        {
            "analysis_complete": False,
            "actions": [{"action": "delete_file", "path": "build/out.exe"}],
        }
    )
    assert result["analysis_complete"] is False
    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "delete_file"
    assert result["actions"][0]["path"] == "build/out.exe"


def test_valid_cleanup_with_gitignore_action() -> None:
    """Test that add_to_gitignore action is valid."""
    result = normalize_commit_cleanup_content(
        {
            "analysis_complete": False,
            "actions": [{"action": "add_to_gitignore", "pattern": "*.exe"}],
        }
    )
    assert result["analysis_complete"] is False
    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "add_to_gitignore"
    assert result["actions"][0]["pattern"] == "*.exe"


def test_valid_cleanup_with_git_exclude_action() -> None:
    """Test that add_to_git_exclude action is valid."""
    result = normalize_commit_cleanup_content(
        {
            "analysis_complete": False,
            "actions": [{"action": "add_to_git_exclude", "pattern": ".env.local"}],
        }
    )
    assert result["analysis_complete"] is False
    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "add_to_git_exclude"
    assert result["actions"][0]["pattern"] == ".env.local"


def test_invalid_action_type_rejected() -> None:
    """Test that unknown action types are rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content(
            {
                "analysis_complete": False,
                "actions": [{"action": "rename_file", "path": "x"}],
            }
        )


def test_delete_file_action_requires_path() -> None:
    """Test that delete_file action requires a path field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content(
            {
                "analysis_complete": False,
                "actions": [{"action": "delete_file"}],
            }
        )


def test_gitignore_action_requires_pattern() -> None:
    """Test that add_to_gitignore action requires a pattern field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content(
            {
                "analysis_complete": False,
                "actions": [{"action": "add_to_gitignore"}],
            }
        )


def test_extra_fields_forbidden() -> None:
    """Test that extra fields beyond the schema are rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content(
            {
                "analysis_complete": True,
                "actions": [],
                "unknown_field": "x",
            }
        )


def test_analysis_complete_required() -> None:
    """Test that analysis_complete field is required."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content({})


def test_git_exclude_action_requires_pattern() -> None:
    """Test that add_to_git_exclude action requires a pattern field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content(
            {
                "analysis_complete": False,
                "actions": [{"action": "add_to_git_exclude"}],
            }
        )


def test_multiple_actions_valid() -> None:
    """Test that multiple actions are valid."""
    result = normalize_commit_cleanup_content(
        {
            "analysis_complete": False,
            "actions": [
                {"action": "delete_file", "path": "build/out.exe"},
                {"action": "add_to_gitignore", "pattern": "*.pyc"},
                {"action": "add_to_git_exclude", "pattern": ".env.local"},
            ],
        }
    )
    assert len(result["actions"]) == 3


def test_reason_is_optional() -> None:
    """Test that the optional reason field is accepted."""
    result = normalize_commit_cleanup_content(
        {
            "analysis_complete": True,
            "actions": [],
            "reason": "All cleanup complete",
        }
    )
    assert result["reason"] == "All cleanup complete"


def test_delete_file_path_rejects_newline() -> None:
    """A newline in ``path`` must be rejected at Pydantic validation time.

    Newlines in a path are the canonical newline-injection vector: a
    multiline ``path`` could plant additional ``.gitignore`` or
    ``.git/info/exclude`` rules. The hardening rejects control characters
    via the Field pattern check.
    """
    with pytest.raises(ValidationError):
        CommitCleanupAction(action="delete_file", path="foo\nbar")


def test_add_to_gitignore_pattern_rejects_tab() -> None:
    """A tab character in ``pattern`` must be rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        CommitCleanupAction(action="add_to_gitignore", pattern="*.py\t")


def test_add_to_gitignore_pattern_rejects_null_byte() -> None:
    """A null byte in ``pattern`` must be rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        CommitCleanupAction(action="add_to_gitignore", pattern="*.\x00py")


def test_delete_file_path_rejects_comment_injection() -> None:
    """A ``#``-prefixed ``path`` must be rejected.

    A ``#`` prefix would silently disable a real ``.gitignore`` or
    ``.git/info/exclude`` rule (both files treat ``#`` lines as comments).
    The hardening rejects ``#``-prefixed values via the validator so a
    malformed artifact cannot silently break the exclude list.
    """
    with pytest.raises(ValidationError):
        CommitCleanupAction(action="delete_file", path="#comment-injection")


def test_add_to_gitignore_pattern_rejects_comment_prefix() -> None:
    """A ``#``-prefixed ``pattern`` must be rejected at Pydantic validation time."""
    with pytest.raises(ValidationError):
        CommitCleanupAction(action="add_to_gitignore", pattern="#*.pyc")


def test_delete_file_path_rejects_whitespace_only() -> None:
    """A whitespace-only ``path`` must be rejected at Pydantic validation time.

    Whitespace-only values would otherwise be silently dropped by the
    classifier (``_classify_action`` skips them with a DEBUG log entry).
    The hardening rejects them at Pydantic validation time so the agent
    gets a clear schema error instead of a silent-drop ambiguity.
    """
    with pytest.raises(ValidationError):
        CommitCleanupAction(action="delete_file", path="   ")


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
        obj = CommitCleanupAction(action=action, path=kwargs["path"])
        assert obj.action == action
        assert obj.path == kwargs["path"]
    else:
        obj = CommitCleanupAction(action=action, pattern=kwargs["pattern"])
        assert obj.action == action
        assert obj.pattern == kwargs["pattern"]


# --- Phase 6 edge-case tests for CommitCleanupAction artifact validator ---
#
# Each test pins one observable behavior of the Pydantic validators on
# CommitCleanupAction. The names follow the plan: test_<unit>_<behavior>
# so the test id maps directly to the action-validator contract.


@pytest.mark.parametrize(
    ("boundary_char", "expected_accepted"),
    [
        # space (0x20): just below the printable-ASCII range -- rejected
        ("\x20", False),
        # 0x21 (!): start of the printable-ASCII range -- accepted
        ("\x21", True),
        # 0x7e (~): end of the printable-ASCII range -- accepted
        ("\x7e", True),
        # 0x7f (DEL): just above the printable-ASCII range -- rejected
        ("\x7f", False),
        # 0x80: first non-ASCII byte -- rejected
        ("\x80", False),
        # 0xff: top of the non-ASCII range -- rejected
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
def test_commit_cleanup_action_rejects_printable_ascii_boundaries(
    boundary_char: str, expected_accepted: bool
) -> None:
    """Boundary chars in the printable-ASCII regex are correctly accepted/rejected.

    Pins the behavior of the ``^[\x21-\x7e]+$`` regex at its six
    boundary positions: chars inside the range (0x21, 0x7e) are
    accepted; chars just outside the range (0x20, 0x7f) and non-ASCII
    chars (0x80, 0xff) are rejected. Both ``path`` and ``pattern`` are
    subject to the same validator; the parametrize runs the assertion
    for each char through both code paths.
    """
    for action_type, kwarg in (
        ("delete_file", "path"),
        ("add_to_gitignore", "pattern"),
        ("add_to_git_exclude", "pattern"),
    ):
        kwargs: dict[str, str] = {"action": action_type, kwarg: boundary_char}
        if expected_accepted:
            # Printable-ASCII boundary IN the range -- must validate.
            obj = CommitCleanupAction(**kwargs)
            assert getattr(obj, kwarg) == boundary_char
        else:
            # Printable-ASCII boundary OUT of the range -- must reject.
            with pytest.raises(ValidationError):
                CommitCleanupAction(**kwargs)


def test_commit_cleanup_action_model_validator_passes_when_both_path_and_pattern_set() -> None:
    """The model_validator does not check that path/pattern are mutually exclusive.

    For ``delete_file``, ``path`` is required; the model_validator does
    NOT require ``pattern`` to be None. The extra ``pattern`` value is
    silently kept on the model but never consulted by the cleanup
    classifier (which only reads ``action.path`` for delete actions).
    For ``add_to_gitignore`` and ``add_to_git_exclude``, ``pattern`` is
    required; the model_validator does NOT require ``path`` to be None.
    The extra ``path`` is silently kept on the model and ignored.
    """
    obj = CommitCleanupAction(action="delete_file", path="foo", pattern="bar")
    assert obj.action == "delete_file"
    assert obj.path == "foo"
    assert obj.pattern == "bar"

    obj2 = CommitCleanupAction(action="add_to_gitignore", path="extra", pattern="*.pyc")
    assert obj2.action == "add_to_gitignore"
    assert obj2.path == "extra"
    assert obj2.pattern == "*.pyc"


def test_commit_cleanup_reason_is_currently_unvalidated() -> None:
    """The ``reason`` field on ``CommitCleanup`` has no field_validator.

    Pins the current behavior: ``reason`` is a free-form ``str | None``
    field on the ``CommitCleanup`` model, NOT a ``CommitCleanupAction``.
    NUL bytes, CJK characters, control chars, and newlines are ALL
    accepted as-is. A future tightening that adds a printable-ASCII
    validator on ``reason`` should add a sibling test (NOT modify this
    one) so the behavior change is explicit.
    """
    for raw in ("\x00nul-injection", "中文说明", "multi\nline\nreason", "\r\nwindows-newline"):
        artifact = CommitCleanup(
            analysis_complete=True, actions=[], reason=raw
        )
        assert artifact.reason == raw, (
            f"reason={raw!r} must be accepted unchanged (no field_validator), "
            f"got: {artifact.reason!r}"
        )


def test_commit_cleanup_action_accepts_none_path_and_pattern() -> None:
    """``path`` and ``pattern`` accept ``None`` -- the field_validators short-circuit.

    Pins the current behavior: when ``path`` is ``None`` (e.g. on an
    ``add_to_gitignore`` action), the field_validator returns ``None``
    unchanged without running the printable-ASCII check. Same for
    ``pattern`` on a ``delete_file`` action. The model_validator is
    what enforces the action-type-vs-required-field contract.
    """
    obj = CommitCleanupAction(action="delete_file", path="foo", pattern=None)
    assert obj.path == "foo"
    assert obj.pattern is None

    obj2 = CommitCleanupAction(action="add_to_gitignore", path=None, pattern="*.pyc")
    assert obj2.path is None
    assert obj2.pattern == "*.pyc"


def test_commit_cleanup_action_rejects_empty_string_path() -> None:
    """Empty string fails the printable-ASCII regex (no one-or-more match).

    Pins the current behavior: the regex is ``^[\x21-\x7e]+$`` -- the
    ``+`` (one-or-more) qualifier means the empty string is rejected
    by the field_validator. A ``delete_file`` action with ``path=""``
    raises ``ValidationError`` from the field_validator before the
    model_validator runs (and would also be rejected by the
    model_validator, which requires ``path`` for ``delete_file``).
    """
    with pytest.raises(ValidationError):
        CommitCleanupAction(action="delete_file", path="")
    with pytest.raises(ValidationError):
        CommitCleanupAction(action="add_to_gitignore", pattern="")

