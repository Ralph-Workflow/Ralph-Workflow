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

from loguru import logger

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
_COMMIT_KINDS = (
    "feat",
    "fix",
    "docs",
    "refactor",
    "test",
    "style",
    "perf",
    "build",
    "ci",
    "chore",
)
_COMMIT_SCOPE_PATTERN = re.compile(r"[a-z0-9/_-]+")
_QUOTED_VALUE_PATTERN = re.compile(r"([\"']).*\1", re.DOTALL)
_COMMIT_SUBJECT_PATTERN = re.compile(
    rf"^({'|'.join(_COMMIT_KINDS)})"
    rf"(\({_COMMIT_SCOPE_PATTERN.pattern}\))?(!)?: [a-z0-9].+"
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
    kind = cast(
        "str", normalized["type"]
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    if kind == _SKIP_KIND:
        return f"{_SKIP_PREFIX} {cast('str', normalized['reason'])}"  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)

    subject = cast(
        "str", normalized["subject"]
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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


#: Fields naming a file on disk. A NUL is dropped from prose (below) but a
#: path is REJECTED: stripping ``src/se<NUL>cret.env`` would silently name
#: ``src/secret.env`` -- a different, real file -- and quietly stage or
#: exclude the wrong one.
_PATH_FIELDS = frozenset({"path", "files"})


def _reject_nul_path(value: str, field: str) -> str:
    """Raise when a path field carries a NUL rather than rewriting the path."""
    if "\x00" in value:
        raise ValueError(
            f"commit_message field {field!r} must not contain an embedded NUL: {value!r}"
        )
    return value


def _strip_nul(value: str, field: str) -> str:
    """Drop NUL characters from prose, which git writes into a corrupt object.

    A NUL reaches a commit body the same way it reached an agent prompt:
    the agent quotes a line of source that holds a literal NUL. Git does
    not reject it -- ``index.commit`` writes it through, ``git log``
    truncates the message at that byte, and ``git fsck`` reports
    ``nulInCommit`` forever after (a server with
    ``fsck.nulInCommit=error`` then refuses the push). Nothing legitimate
    puts one in a commit message, and rejecting the artifact would only
    send the agent round the submission loop again, so drop them and say
    so. Path fields take the opposite treatment -- see ``_PATH_FIELDS``.
    """
    if field in _PATH_FIELDS:
        return _reject_nul_path(value, field)
    if "\x00" not in value:
        return value
    logger.warning(
        f"commit_message field {field!r}: dropped {value.count(chr(0))} NUL character(s); "
        f"git would have written a commit object that git fsck reports as nulInCommit"
    )
    return value.replace("\x00", "")


def _required_string_field(content: dict[str, object], field: str) -> str:
    value = content.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"commit_message payloads require a non-empty '{field}'")
    normalized = _strip_nul(value, field).strip()
    if field == "subject":
        _validate_commit_subject(normalized)
    return normalized


def _validate_commit_subject(subject: str) -> None:
    if _COMMIT_SUBJECT_PATTERN.fullmatch(subject):
        return
    raise ValueError(
        f"commit_message subject {subject!r} does not use conventional commit format: "
        f"{_diagnose_commit_subject(subject)}; rewrite it as "
        "'<kind>(<scope>)?!?: <lowercase description>', for example "
        "'fix(parser): preserve prefixed transcript lines'"
    )


def _diagnose_commit_subject(subject: str) -> str:
    """Name the single reason this subject was rejected.

    One generic message for every cause makes an agent guess and rewrite the
    subject repeatedly; each branch below states the one edit that fixes it.
    """
    if _QUOTED_VALUE_PATTERN.fullmatch(subject):
        return (
            "the value is wrapped in surrounding quotes, which frontmatter takes "
            "literally as part of the subject — remove them"
        )

    prefix, separator, description = subject.partition(": ")
    if not separator:
        return "it has no 'kind: description' separator (a colon followed by one space)"

    return _diagnose_commit_prefix(prefix) or _diagnose_commit_description(description)


def _diagnose_commit_prefix(prefix: str) -> str | None:
    """Return why the ``kind(scope)!`` half is invalid, or None when it is valid."""
    kind = prefix.removesuffix("!")
    scope = ""
    if kind.endswith(")"):
        kind, _, scope_text = kind.partition("(")
        scope = scope_text.removesuffix(")")

    if kind not in _COMMIT_KINDS:
        if kind.lower() in _COMMIT_KINDS:
            return f"kinds are lowercase, so write {kind.lower()!r} rather than {kind!r}"
        return f"{kind!r} is not one of the allowed kinds: {', '.join(_COMMIT_KINDS)}"

    if scope and not _COMMIT_SCOPE_PATTERN.fullmatch(scope):
        return f"the scope {scope!r} may only contain lowercase letters, digits, '/', '_' and '-'"

    return None


def _diagnose_commit_description(description: str) -> str:
    """Return why the description half is invalid; the caller knows it is."""
    if not description:
        return "the description after the colon is empty"
    return "the description must start with a lowercase letter or digit"


def _optional_string_field(content: dict[str, object], field: str) -> str | None:
    value = content.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"commit_message field '{field}' must be a non-empty string when provided")
    return _strip_nul(value, field).strip()


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
        normalized.append(_reject_nul_path(item, field).strip())
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
