"""Unit tests for CommitCleanup artifact model and normalizer."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts._typed_artifact_validation_error import TypedArtifactValidationError
from ralph.mcp.artifacts.typed_artifacts import normalize_commit_cleanup_content


def test_valid_cleanup_done_artifact() -> None:
    """Test that analysis_complete=True with empty actions is valid."""
    result = normalize_commit_cleanup_content({"analysis_complete": True, "actions": []})
    assert result["analysis_complete"] is True
    assert result["actions"] == []


def test_valid_cleanup_with_delete_action() -> None:
    """Test that delete_file action is valid."""
    result = normalize_commit_cleanup_content({
        "analysis_complete": False,
        "actions": [{"action": "delete_file", "path": "build/out.exe"}],
    })
    assert result["analysis_complete"] is False
    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "delete_file"
    assert result["actions"][0]["path"] == "build/out.exe"


def test_valid_cleanup_with_gitignore_action() -> None:
    """Test that add_to_gitignore action is valid."""
    result = normalize_commit_cleanup_content({
        "analysis_complete": False,
        "actions": [{"action": "add_to_gitignore", "pattern": "*.exe"}],
    })
    assert result["analysis_complete"] is False
    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "add_to_gitignore"
    assert result["actions"][0]["pattern"] == "*.exe"


def test_valid_cleanup_with_git_exclude_action() -> None:
    """Test that add_to_git_exclude action is valid."""
    result = normalize_commit_cleanup_content({
        "analysis_complete": False,
        "actions": [{"action": "add_to_git_exclude", "pattern": ".env.local"}],
    })
    assert result["analysis_complete"] is False
    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "add_to_git_exclude"
    assert result["actions"][0]["pattern"] == ".env.local"


def test_invalid_action_type_rejected() -> None:
    """Test that unknown action types are rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content({
            "analysis_complete": False,
            "actions": [{"action": "rename_file", "path": "x"}],
        })


def test_delete_file_action_requires_path() -> None:
    """Test that delete_file action requires a path field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content({
            "analysis_complete": False,
            "actions": [{"action": "delete_file"}],
        })


def test_gitignore_action_requires_pattern() -> None:
    """Test that add_to_gitignore action requires a pattern field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content({
            "analysis_complete": False,
            "actions": [{"action": "add_to_gitignore"}],
        })


def test_extra_fields_forbidden() -> None:
    """Test that extra fields beyond the schema are rejected."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content({
            "analysis_complete": True,
            "actions": [],
            "unknown_field": "x",
        })


def test_analysis_complete_required() -> None:
    """Test that analysis_complete field is required."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content({})


def test_git_exclude_action_requires_pattern() -> None:
    """Test that add_to_git_exclude action requires a pattern field."""
    with pytest.raises(TypedArtifactValidationError):
        normalize_commit_cleanup_content({
            "analysis_complete": False,
            "actions": [{"action": "add_to_git_exclude"}],
        })


def test_multiple_actions_valid() -> None:
    """Test that multiple actions are valid."""
    result = normalize_commit_cleanup_content({
        "analysis_complete": False,
        "actions": [
            {"action": "delete_file", "path": "build/out.exe"},
            {"action": "add_to_gitignore", "pattern": "*.pyc"},
            {"action": "add_to_git_exclude", "pattern": ".env.local"},
        ],
    })
    assert len(result["actions"]) == 3


def test_reason_is_optional() -> None:
    """Test that the optional reason field is accepted."""
    result = normalize_commit_cleanup_content({
        "analysis_complete": True,
        "actions": [],
        "reason": "All cleanup complete",
    })
    assert result["reason"] == "All cleanup complete"
