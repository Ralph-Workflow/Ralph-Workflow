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
    render_commit_message_content,
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


@pytest.mark.parametrize(
    ("subject", "expected_cause"),
    [
        ('"fix(auth): prevent token expiry race"', "surrounding quotes"),
        ("update files", "no 'kind: description' separator"),
        ("Fix(auth): prevent race", "kinds are lowercase"),
        ("wibble(auth): prevent race", "not one of the allowed kinds"),
        ("fix(MCP): prevent race", "scope 'MCP' may only contain"),
        ("fix(mcp.artifacts): prevent race", "scope 'mcp.artifacts' may only contain"),
        ("fix(auth): Prevent race", "description must start with a lowercase letter or digit"),
        ("fix(auth): ", "no 'kind: description' separator"),
    ],
)
def test_rejected_subject_names_its_exact_cause(subject: str, expected_cause: str) -> None:
    """One generic rejection for every cause makes weak agents guess; name the cause."""
    with pytest.raises(ValueError) as excinfo:
        normalize_commit_message_content({"type": "commit", "subject": subject})

    message = str(excinfo.value)
    assert expected_cause in message, message
    assert subject.strip() in message, (
        f"the rejection must echo the offending subject so the agent sees it: {message!r}"
    )


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


def test_render_commit_message_content_drops_a_nul_from_the_body() -> None:
    """Git writes a NUL straight into the commit object: ``git log`` then
    truncates the message at that byte and ``git fsck`` reports nulInCommit
    forever, so the message must never carry one."""
    rendered = render_commit_message_content(
        {
            "type": "commit",
            "subject": "fix(auth): prevent token expiry race",
            "body": "Serialize refresh\x00INJECTED so a concurrent refresh cannot lose a token.",
        }
    )

    assert "\x00" not in rendered
    assert rendered.endswith(
        "Serialize refreshINJECTED so a concurrent refresh cannot lose a token."
    )


def test_render_commit_message_content_leaves_a_clean_body_untouched() -> None:
    body = "Serialize refresh so a concurrent refresh cannot lose a token."
    rendered = render_commit_message_content(
        {"type": "commit", "subject": "fix(auth): prevent token expiry race", "body": body}
    )

    assert rendered == f"fix(auth): prevent token expiry race\n\n{body}"


def test_render_commit_message_content_drops_a_nul_from_the_subject() -> None:
    """The subject is prose too, and it is the line every git reader shows."""
    rendered = render_commit_message_content(
        {"type": "commit", "subject": "fix(auth): prevent a token\x00 race"}
    )

    assert rendered == "fix(auth): prevent a token race"


def test_normalize_commit_message_content_rejects_a_nul_in_a_path_field() -> None:
    """A path is not prose: stripping ``src/se<NUL>cret.env`` would silently
    name ``src/secret.env`` — a different, real file — and exclude the wrong one."""
    for payload in (
        {"type": "commit", "subject": "fix(auth): scope the commit", "files": ["src/au\x00th.py"]},
        {
            "type": "commit",
            "subject": "fix(auth): scope the commit",
            "excluded_files": [{"path": "src/se\x00cret.env", "reason": "generated"}],
        },
    ):
        with pytest.raises(ValueError, match="embedded NUL"):
            normalize_commit_message_content(payload)
