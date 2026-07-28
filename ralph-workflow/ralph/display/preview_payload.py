"""Normalize known file-activity tool events into pure preview payloads.

This module is deliberately parser-agnostic: parsers preserve their documented
``input``/``args`` metadata shapes and this boundary converts only recognized
native and Ralph file tools. Unknown tools return ``None``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final

_PATCH_HUNK_RE: Final[re.Pattern[str]] = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", re.MULTILINE)


@dataclass(frozen=True)
class PreviewHunk:
    """One ordered old/new content fragment with an optional file line."""

    old_text: str = ""
    new_text: str = ""
    start_line: int | None = None


@dataclass(frozen=True)
class PreviewPayload:
    """Canonical, display-safe description of known file activity."""

    path: str | None
    language_hint: str | None
    operation: str
    hunks: tuple[PreviewHunk, ...] = ()
    content: str | None = None


def _mapping(value: object) -> dict[str, object] | None:
    """Return a mapping directly or from a JSON object string."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed: object = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _input(metadata: dict[str, object]) -> dict[str, object] | None:
    """Extract the documented parser input/args envelope without guessing."""
    for key in ("input", "args", "arguments"):
        payload = _mapping(metadata.get(key))
        if payload is not None:
            return payload
    return None


def _path(payload: dict[str, object]) -> str | None:
    for key in ("path", "file_path", "filePath", "filename"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _patch_hunks(patch: str) -> tuple[PreviewHunk, ...]:
    """Extract added/removed hunk bodies and real new-file line numbers."""
    matches = list(_PATCH_HUNK_RE.finditer(patch))
    if not matches:
        return ()
    hunks: list[PreviewHunk] = []
    for index, match in enumerate(matches):
        body = patch[match.end() : matches[index + 1].start() if index + 1 < len(matches) else None]
        old = "\n".join(line[1:] for line in body.splitlines() if line.startswith("-") and not line.startswith("---"))
        new = "\n".join(line[1:] for line in body.splitlines() if line.startswith("+") and not line.startswith("+++"))
        hunks.append(PreviewHunk(old, new, int(match.group(1))))
    return tuple(hunks)


def payload_from_tool_event(  # noqa: PLR0911 -- tool names are a closed normalization table
    tool_name: str, metadata: dict[str, object]
) -> PreviewPayload | None:
    """Normalize a recognized file tool event; leave every unknown tool alone."""
    payload = _input(metadata)
    if payload is None:
        return None
    bare = tool_name.removeprefix("mcp__ralph__").removeprefix("ralph.")
    path = _path(payload)
    if bare in {"read_file", "Read"}:
        return PreviewPayload(path, None, "read", content=payload.get("content") if isinstance(payload.get("content"), str) else None)
    if bare in {"write_file", "Write", "ralph_stage_md_artifact", "ralph_submit_md_artifact"}:
        content = payload.get("content")
        return PreviewPayload(path, None, "write", content=content if isinstance(content, str) else None)
    if bare in {"append_file", "Append"}:
        content = payload.get("content")
        return PreviewPayload(path, None, "append", content=content if isinstance(content, str) else None)
    if bare in {"edit_file", "Edit", "str_replace", "ralph_edit_md_artifact"}:
        edits = payload.get("edits")
        if not isinstance(edits, list):
            edits = [payload]
        hunks = tuple(
            PreviewHunk(str(item.get("oldText", item.get("old_string", "")) or ""), str(item.get("newText", item.get("new_string", "")) or ""), item.get("start_line") if isinstance(item.get("start_line"), int) else None)
            for item in edits if isinstance(item, dict)
        )
        return PreviewPayload(path, None, "replace", hunks=hunks)
    if bare == "MultiEdit":
        edits = payload.get("edits")
        if not isinstance(edits, list):
            return None
        hunks = tuple(PreviewHunk(str(item.get("old_string", "") or ""), str(item.get("new_string", "") or "")) for item in edits if isinstance(item, dict))
        return PreviewPayload(path, None, "replace", hunks=hunks)
    if bare == "NotebookEdit":
        source = payload.get("new_source", payload.get("content", payload.get("source")))
        language = payload.get("kernel", payload.get("language"))
        return PreviewPayload(path, language if isinstance(language, str) else None, "write", content=source if isinstance(source, str) else None)
    if bare == "apply_patch":
        patch = payload.get("patch", payload.get("input"))
        if not isinstance(patch, str):
            return None
        return PreviewPayload(path, "diff", "patch", hunks=_patch_hunks(patch), content=patch)
    return None


__all__ = ["PreviewHunk", "PreviewPayload", "payload_from_tool_event"]
