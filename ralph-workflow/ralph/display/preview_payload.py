"""Normalize recognized file tool events into canonical preview payloads.

The display layer uses this parser-agnostic boundary rather than learning each
agent parser's metadata shape. Unknown tools and malformed JSON deliberately
produce ``None``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final

_PATCH_HUNK_RE: Final[re.Pattern[str]] = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", re.MULTILINE
)


@dataclass(frozen=True)
class _PreviewHunk:
    """One ordered old/new fragment and its optional absolute new-file line."""

    old_text: str = ""
    new_text: str = ""
    start_line: int | None = None


type PreviewHunk = _PreviewHunk
"""Public type alias for a canonical preview hunk."""


@dataclass(frozen=True)
class PreviewPayload:
    """Canonical display-safe description of recognized file activity."""

    path: str | None
    language_hint: str | None
    operation: str
    hunks: tuple[PreviewHunk, ...] = ()
    content: str | None = None


def _mapping(value: object) -> dict[str, object] | None:
    """Return a string-keyed mapping directly or from a JSON object string."""
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(parsed, dict):
        return None
    return {key: value for key, value in parsed.items() if isinstance(key, str)}


def _input(metadata: dict[str, object]) -> dict[str, object] | None:
    """Extract only the documented parser input/args envelopes."""
    for key in ("input", "args", "arguments"):
        payload = _mapping(metadata.get(key))
        if payload is not None:
            return payload
    return None


def _path(payload: dict[str, object]) -> str | None:
    """Return a known file-path field without inferring arbitrary keys."""
    for key in ("path", "file_path", "filePath", "filename"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _line(value: object) -> int | None:
    """Return a positive integer source line, excluding bool."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _patch_hunks(patch: str) -> tuple[PreviewHunk, ...]:
    """Extract unified-diff bodies with real new-file line numbers."""
    matches = list(_PATCH_HUNK_RE.finditer(patch))
    hunks: list[PreviewHunk] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(patch)
        body = patch[match.end() : end]
        old = "\n".join(line[1:] for line in body.splitlines() if line.startswith("-") and not line.startswith("---"))
        new = "\n".join(line[1:] for line in body.splitlines() if line.startswith("+") and not line.startswith("+++"))
        hunks.append(_PreviewHunk(old, new, int(match.group(1))))
    return tuple(hunks)


def _edit_hunks(payload: dict[str, object], *, multiple: bool) -> tuple[PreviewHunk, ...]:
    """Normalize one edit or an ordered ``edits`` array."""
    edits = payload.get("edits")
    items: tuple[object, ...] = tuple(edits) if isinstance(edits, list) else (() if multiple else (payload,))
    hunks: list[PreviewHunk] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        old = item.get("oldText", item.get("old_string", ""))
        new = item.get("newText", item.get("new_string", ""))
        hunks.append(
            _PreviewHunk(
                old if isinstance(old, str) else "",
                new if isinstance(new, str) else "",
                _line(item.get("start_line")),
            )
        )
    return tuple(hunks)


def _content_payload(
    operation: str, path: str | None, payload: dict[str, object]
) -> PreviewPayload:
    """Build the canonical payload for a whole-content operation."""
    content = payload.get("content")
    return PreviewPayload(path, None, operation, content=content if isinstance(content, str) else None)


def _notebook_payload(path: str | None, payload: dict[str, object]) -> PreviewPayload:
    """Build a notebook-cell preview using its declared kernel language."""
    source = payload.get("new_source", payload.get("content", payload.get("source")))
    language = payload.get("kernel", payload.get("language"))
    return PreviewPayload(
        path,
        language if isinstance(language, str) else None,
        "write",
        content=source if isinstance(source, str) else None,
    )


def _patch_payload(path: str | None, payload: dict[str, object]) -> PreviewPayload | None:
    """Build a unified-diff payload when the documented patch string exists."""
    patch = payload.get("patch", payload.get("input"))
    return PreviewPayload(path, "diff", "patch", _patch_hunks(patch), patch) if isinstance(patch, str) else None


def payload_from_tool_event(tool_name: str, metadata: dict[str, object]) -> PreviewPayload | None:
    """Normalize a recognized native or Ralph file tool event, else ``None``."""
    payload = _input(metadata)
    if payload is None:
        return None
    bare = tool_name.removeprefix("mcp__ralph__").removeprefix("ralph.")
    path = _path(payload)
    operation = {
        "read_file": "read", "Read": "read", "write_file": "write", "Write": "write",
        "append_file": "append", "Append": "append", "ralph_stage_md_artifact": "write",
        "ralph_submit_md_artifact": "write",
    }.get(bare)
    if operation is not None:
        return _content_payload(operation, path, payload)
    if bare in {"edit_file", "Edit", "str_replace", "ralph_edit_md_artifact"}:
        return PreviewPayload(path, None, "replace", _edit_hunks(payload, multiple=False))
    if bare == "MultiEdit":
        return PreviewPayload(path, None, "replace", _edit_hunks(payload, multiple=True))
    if bare == "NotebookEdit":
        return _notebook_payload(path, payload)
    return _patch_payload(path, payload) if bare == "apply_patch" else None


__all__ = ["PreviewHunk", "PreviewPayload", "payload_from_tool_event"]
