"""Tests for ralph/mcp/artifacts/commit_message.py — markdown commit artifact helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    normalize_commit_message_content,
    read_commit_message_artifact,
    read_commit_message_from_path,
)

if TYPE_CHECKING:
    from pathlib import Path

COMMIT_DOC = """---
type: commit
subject: feat(api): add report export
---

## Body Summary

- [S-1] Add CSV export for reports.

## Body Details

- [D-1] Supports filtered exports and keeps column order stable.

## Body Footer

- [F-1] Fixes #42
"""

SKIP_DOC = """---
type: skip
reason: No relevant diff
---
"""


def test_commit_message_artifact_is_the_markdown_submission_path() -> None:
    """The commit phase must look where markdown submission writes the artifact."""
    assert COMMIT_MESSAGE_ARTIFACT == ".agent/artifacts/commit_message.md"


def test_read_commit_message_artifact_renders_markdown_commit_document(tmp_path: Path) -> None:
    artifact_file = tmp_path / ".agent" / "artifacts" / "commit_message.md"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(COMMIT_DOC, encoding="utf-8")

    assert read_commit_message_artifact(tmp_path) == (
        "feat(api): add report export\n\n"
        "Add CSV export for reports.\n\n"
        "Supports filtered exports and keeps column order stable.\n\n"
        "Fixes #42"
    )


def test_read_commit_message_artifact_returns_none_when_absent(tmp_path: Path) -> None:
    assert read_commit_message_artifact(tmp_path) is None


def test_read_commit_message_artifact_returns_none_for_invalid_document(tmp_path: Path) -> None:
    artifact_file = tmp_path / ".agent" / "artifacts" / "commit_message.md"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(
        "---\ntype: commit\nsubject: not a conventional subject\n---\n",
        encoding="utf-8",
    )

    assert read_commit_message_artifact(tmp_path) is None


def test_read_commit_message_from_path_formats_markdown_skip_document(tmp_path: Path) -> None:
    message_file = tmp_path / "commit_message.md"
    message_file.write_text(SKIP_DOC, encoding="utf-8")

    assert read_commit_message_from_path(message_file) == "SKIP: No relevant diff"


def test_read_commit_message_from_path_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_commit_message_from_path(tmp_path / "commit_message.md") is None


def test_delete_commit_message_artifacts_removes_only_canonical_markdown(
    tmp_path: Path,
) -> None:
    artifact_file = tmp_path / ".agent" / "artifacts" / "commit_message.md"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(COMMIT_DOC, encoding="utf-8")
    legacy_dir = tmp_path / ".agent" / "tmp"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    unrelated_file = legacy_dir / "worker-state.txt"
    unrelated_file.write_text("keep", encoding="utf-8")

    delete_commit_message_artifacts(tmp_path)

    assert not artifact_file.exists()
    assert unrelated_file.read_text(encoding="utf-8") == "keep"


def test_normalize_commit_message_content_accepts_excluded_files_payload() -> None:
    normalized = normalize_commit_message_content(
        {
            "type": "commit",
            "subject": "fix(core): scope commit staging",
            "excluded_files": [{"path": "docs/guide.md", "reason": "internal_ignore"}],
        }
    )

    assert normalized["excluded_files"] == [{"path": "docs/guide.md", "reason": "internal_ignore"}]


def test_normalize_commit_message_content_rejects_non_conventional_subject() -> None:
    with pytest.raises(ValueError, match="conventional commit format"):
        normalize_commit_message_content({"type": "commit", "subject": "update files"})


def test_normalize_commit_message_content_rejects_legacy_message_field() -> None:
    with pytest.raises(ValueError):
        normalize_commit_message_content({"message": "fix: legacy JSON payload"})


def test_normalize_commit_message_content_rejects_string_payload() -> None:
    with pytest.raises(ValueError, match="dictionary"):
        normalize_commit_message_content("fix: legacy string payload")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "commit",
            "subject": "fix(core): scope commit staging",
            "excluded_files": [{"path": "docs/guide.md", "reason": "generated"}],
        },
        {
            "type": "commit",
            "subject": "fix(core): scope commit staging",
            "excluded_files": ["docs/guide.md"],
        },
    ],
)
def test_normalize_commit_message_content_rejects_invalid_excluded_files_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        normalize_commit_message_content(payload)
