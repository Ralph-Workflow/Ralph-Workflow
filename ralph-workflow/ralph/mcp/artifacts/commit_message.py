"""Commit-message artifact helpers.

The canonical commit message is the markdown artifact the agent submits to
`.agent/artifacts/commit_message.md` (written by
:mod:`ralph.mcp.artifacts.canonical_submit`). The document declares its
`commit` or `skip` variant in frontmatter and is validated by the registered
``commit_message`` markdown spec; these helpers read that document and render
the plain-text commit message consumers pass to git.
"""

from __future__ import annotations

import re
from importlib import import_module
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec

if TYPE_CHECKING:
    from pathlib import Path

COMMIT_MESSAGE_ARTIFACT = ".agent/artifacts/commit_message.md"
COMMIT_MESSAGE_TYPE = "commit_message"
COMMIT_MESSAGE_NAME = "commit_message"
_COMMIT_KIND = "commit"
_SKIP_KIND = "skip"
_SKIP_PREFIX = "SKIP:"
_DETAILED_BODY_KEYS = ("body_summary", "body_details", "body_footer")
_EXCLUDED_FILE_REASONS = frozenset({"internal_ignore", "not_task_related", "sensitive", "deferred"})
_COMMIT_SUBJECT_PATTERN = re.compile(
    r"^(feat|fix|docs|refactor|test|style|perf|build|ci|chore)(\([a-z0-9/_-]+\))?(!)?: [a-z0-9].+"
)


def commit_message_artifact_path(repo_root: Path) -> Path:
    """Return the canonical markdown artifact path for the given repo root."""
    return repo_root / COMMIT_MESSAGE_ARTIFACT


def read_commit_message_artifact(
    repo_root: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> str | None:
    """Read the commit message from the canonical markdown artifact."""
    return read_commit_message_from_path(commit_message_artifact_path(repo_root), backend=backend)


def read_commit_message_from_path(
    message_file: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> str | None:
    """Read a commit message from a markdown artifact document at an arbitrary path."""
    payload = read_commit_message_payload_from_path(message_file, backend=backend)
    if payload is None:
        return None
    return render_commit_message_content(payload)


def read_commit_message_payload_from_path(
    message_file: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> dict[str, object] | None:
    """Read and normalize a commit message payload from a markdown artifact document."""
    if not backend.exists(message_file):
        return None
    return _payload_from_markdown_text(backend.read_text(message_file, encoding="utf-8"))


def _payload_from_markdown_text(text: str) -> dict[str, object] | None:
    """Validate a commit_message markdown document and return its normalized payload."""
    import_module("ralph.mcp.artifacts.markdown.specs")
    content, diagnostics = parse_and_validate(text, get_spec(COMMIT_MESSAGE_TYPE))
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return None
    return content


def delete_commit_message_artifacts(
    repo_root: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> None:
    """Remove the canonical commit-message artifact."""
    artifact_path = commit_message_artifact_path(repo_root)
    if backend.exists(artifact_path):
        backend.unlink(artifact_path)


def normalize_commit_message_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a commit message payload to a canonical dict form."""
    if not isinstance(content, dict):
        raise ValueError("commit_message content must be a dictionary")

    kind = _required_string_field(content, "type")
    if kind == _COMMIT_KIND:
        return _normalize_commit_payload(content)
    if kind == _SKIP_KIND:
        reason = _required_string_field(content, "reason")
        _reject_unknown_fields(content, {"type", "reason"})
        return {"type": _SKIP_KIND, "reason": reason}
    raise ValueError("commit_message content type must be 'commit' or 'skip'")


def render_commit_message_content(content: dict[str, object]) -> str:
    """Render normalized commit message content as a plain-text commit message string."""
    normalized = normalize_commit_message_content(content)
    kind = cast("str", normalized["type"])
    if kind == _SKIP_KIND:
        return f"{_SKIP_PREFIX} {cast('str', normalized['reason'])}"

    subject = cast("str", normalized["subject"])
    body = _render_commit_body(normalized)
    return subject if not body else f"{subject}\n\n{body}"


def _normalize_commit_payload(content: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {
        "type": _COMMIT_KIND,
        "subject": _required_string_field(content, "subject"),
    }

    body = _optional_string_field(content, "body")
    detailed_values = {
        key: value
        for key in _DETAILED_BODY_KEYS
        if (value := _optional_string_field(content, key)) is not None
    }
    if body is not None and detailed_values:
        raise ValueError("Use either 'body' or the detailed body fields, not both")
    if body is not None:
        normalized["body"] = body
    normalized.update(detailed_values)

    files = _optional_string_list(content, "files")
    if files is not None:
        if not files:
            raise ValueError("commit_message 'files' must not be empty when provided")
        normalized["files"] = files

    excluded_files = _optional_excluded_files(content)
    if excluded_files is not None:
        normalized["excluded_files"] = excluded_files

    allowed_fields = {"type", "subject", "body", *_DETAILED_BODY_KEYS, "files", "excluded_files"}
    _reject_unknown_fields(content, allowed_fields)
    return normalized


def _render_commit_body(content: dict[str, object]) -> str:
    body = _optional_string_field(content, "body")
    if body is not None:
        return body

    sections = [
        value
        for key in _DETAILED_BODY_KEYS
        if (value := _optional_string_field(content, key)) is not None
    ]
    return "\n\n".join(sections)


def _required_string_field(content: dict[str, object], field: str) -> str:
    value = content.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"commit_message payloads require a non-empty '{field}'")
    normalized = value.strip()
    if field == "subject":
        _validate_commit_subject(normalized)
    return normalized


def _validate_commit_subject(subject: str) -> None:
    if not _COMMIT_SUBJECT_PATTERN.fullmatch(subject):
        raise ValueError(
            "commit_message subjects must use conventional commit format "
            "like 'fix(parser): preserve prefixed transcript lines'"
        )


def _optional_string_field(content: dict[str, object], field: str) -> str | None:
    value = content.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"commit_message field '{field}' must be a non-empty string when provided")
    return value.strip()


def _optional_string_list(content: dict[str, object], field: str) -> list[str] | None:
    value = content.get(field)
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"commit_message field '{field}' must be an array of strings")

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"commit_message field '{field}' must contain only non-empty strings")
        normalized.append(item.strip())
    return normalized


def _optional_excluded_files(content: dict[str, object]) -> list[dict[str, object]] | None:
    value = content.get("excluded_files")
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("commit_message field 'excluded_files' must be an array")

    normalized: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("commit_message 'excluded_files' entries must be objects")
        path = _required_string_field(item, "path")
        reason = _required_string_field(item, "reason")
        if reason not in _EXCLUDED_FILE_REASONS:
            raise ValueError(
                "commit_message excluded_files reason must be one of "
                + ", ".join(sorted(_EXCLUDED_FILE_REASONS))
            )
        _reject_unknown_fields(item, {"path", "reason"})
        normalized.append({"path": path, "reason": reason})
    return normalized


def _reject_unknown_fields(content: dict[str, object], allowed: set[str]) -> None:
    unexpected = sorted(key for key in content if key not in allowed)
    if unexpected:
        formatted = ", ".join(unexpected)
        raise ValueError(f"commit_message payload contains unsupported field(s): {formatted}")
