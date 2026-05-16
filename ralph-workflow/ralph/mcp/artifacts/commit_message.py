"""Commit-message artifact helpers.

Canonical commit messages are stored as MCP-style JSON artifacts in
`.agent/tmp/commit_message.json`. The commit artifact content follows a
structured schema with either a `commit` or `skip` variant. A plain-text
mirror in `.agent/tmp/commit-message.txt` is maintained for CLI compatibility.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.store import Artifact

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

COMMIT_MESSAGE_ARTIFACT = ".agent/tmp/commit_message.json"
COMMIT_MESSAGE_TEXT = ".agent/tmp/commit-message.txt"
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


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def commit_message_artifact_path(repo_root: Path) -> Path:
    """Return the canonical artifact JSON path for the given repo root."""
    return repo_root / COMMIT_MESSAGE_ARTIFACT


def commit_message_text_path(repo_root: Path) -> Path:
    """Return the plain-text mirror path for commit messages."""
    return repo_root / COMMIT_MESSAGE_TEXT


def write_commit_message_artifact(
    repo_root: Path,
    message: str | dict[str, object],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    now_iso: Callable[[], str] = _now_iso,
) -> None:
    """Persist a commit message as both a JSON artifact and a plain-text file."""
    artifact_path = commit_message_artifact_path(repo_root)
    text_path = commit_message_text_path(repo_root)
    backend.mkdir(artifact_path.parent, parents=True, exist_ok=True)
    backend.mkdir(text_path.parent, parents=True, exist_ok=True)

    normalized = normalize_commit_message_content(message)
    timestamp = now_iso()

    artifact = Artifact(
        name=COMMIT_MESSAGE_NAME,
        artifact_type=COMMIT_MESSAGE_TYPE,
        content=normalized,
        created_at=timestamp,
        updated_at=timestamp,
    )
    backend.write_text(artifact_path, json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")
    backend.write_text(text_path, render_commit_message_content(normalized), encoding="utf-8")


def read_commit_message_artifact(
    repo_root: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> str | None:
    """Read the commit message from the canonical artifact, falling back to the text file."""
    artifact_path = commit_message_artifact_path(repo_root)
    if backend.exists(artifact_path):
        parsed = _read_commit_message_text_from_json_path(artifact_path, backend=backend)
        if parsed is not None:
            return parsed

    text_path = commit_message_text_path(repo_root)
    if not backend.exists(text_path):
        return None
    contents = backend.read_text(text_path, encoding="utf-8").strip()
    return contents or None


def read_commit_message_from_path(
    message_file: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> str | None:
    """Read a commit message from an arbitrary file path (JSON or plain text)."""
    payload = read_commit_message_payload_from_path(message_file, backend=backend)
    if payload is not None:
        return render_commit_message_content(payload)

    if not backend.exists(message_file) or message_file.suffix == ".json":
        return None
    contents = backend.read_text(message_file, encoding="utf-8").strip()
    return contents or None



def read_commit_message_payload_from_path(
    message_file: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> dict[str, object] | None:
    """Read and normalize a commit message payload from JSON or plain text."""
    if message_file.suffix == ".json":
        if not backend.exists(message_file):
            return None
        return _read_commit_message_payload_from_json_path(message_file, backend=backend)

    if not backend.exists(message_file):
        return None
    contents = backend.read_text(message_file, encoding="utf-8").strip()
    if not contents:
        return None
    try:
        return normalize_commit_message_content(contents)
    except ValueError:
        return None


_LEGACY_STALE_GLOBS = (
    "commit_message.xml.processed",
    "commit_message.xsd",
    "commit_diff.txt",
    "commit_diff.model_safe.txt",
)


def delete_commit_message_artifacts(
    repo_root: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> None:
    """Remove all commit message artifacts and legacy stale files."""
    for path in (commit_message_artifact_path(repo_root), commit_message_text_path(repo_root)):
        if backend.exists(path):
            backend.unlink(path)

    tmp_dir = repo_root / ".agent" / "tmp"
    for name in _LEGACY_STALE_GLOBS:
        stale = tmp_dir / name
        if backend.exists(stale):
            backend.unlink(stale)


def normalize_commit_message_content(content: str | dict[str, object]) -> dict[str, object]:
    """Validate and normalize a commit message payload to a canonical dict form."""
    if isinstance(content, str):
        stripped = content.strip()
        if not stripped:
            raise ValueError("commit_message content cannot be empty")
        if stripped.upper().startswith(_SKIP_PREFIX):
            reason = stripped[len(_SKIP_PREFIX) :].strip()
            if not reason:
                raise ValueError("skip commit_message content requires a reason")
            return {"type": _SKIP_KIND, "reason": reason}
        _validate_commit_subject(stripped)
        return {"type": _COMMIT_KIND, "subject": stripped}

    if not isinstance(content, dict):
        raise ValueError("commit_message content must be a dictionary")

    legacy_message = content.get("message")
    if isinstance(legacy_message, str) and legacy_message.strip():
        return normalize_commit_message_content(legacy_message)
    if "message" in content:
        raise ValueError("legacy commit_message payload must use a non-empty 'message' string")

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


def _read_commit_message_text_from_json_path(
    message_file: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> str | None:
    payload = _read_commit_message_payload_from_json_path(message_file, backend=backend)
    if payload is None:
        return None
    return render_commit_message_content(payload)



def _read_commit_message_payload_from_json_path(
    message_file: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> dict[str, object] | None:
    try:
        payload = cast(
            "dict[str, object]", json.loads(backend.read_text(message_file, encoding="utf-8"))
        )
    except (TypeError, json.JSONDecodeError):
        return None
    try:
        artifact = Artifact.from_dict(payload)
    except (KeyError, TypeError, ValueError):
        return _normalize_raw_commit_message_payload(payload)

    if artifact.artifact_type != COMMIT_MESSAGE_TYPE:
        return _normalize_raw_commit_message_payload(payload)

    try:
        return normalize_commit_message_content(artifact.content)
    except ValueError:
        return _normalize_raw_commit_message_payload(payload)



def _render_raw_commit_message_payload(payload: dict[str, object]) -> str | None:
    normalized = _normalize_raw_commit_message_payload(payload)
    if normalized is None:
        return None
    return render_commit_message_content(normalized)



def _normalize_raw_commit_message_payload(payload: dict[str, object]) -> dict[str, object] | None:
    try:
        return normalize_commit_message_content(payload)
    except ValueError:
        return None


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
