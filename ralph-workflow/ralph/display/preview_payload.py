"""Normalize recognized file-activity events into bounded preview payloads.

The display layer consumes these parser-agnostic values rather than branching
on agent parser identity. Unknown tools and malformed envelopes deliberately
return ``None``.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Final, Literal, cast

_PATCH_HUNK_RE: Final[re.Pattern[str]] = re.compile(
    r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@", re.MULTILINE
)
_MAX_LITERAL_PARSE_CHARS: Final[int] = 8_192
PreviewOperation = Literal["read", "write", "append", "replace", "patch"]


@dataclass(frozen=True)
class _PreviewHunk:
    """One ordered old/new fragment with an optional absolute new-file line."""

    old_text: str = ""
    new_text: str = ""
    start_line: int | None = None
    label: str | None = None


@dataclass(frozen=True)
class _PreviewPayload:
    """Canonical, display-safe description of recognized file activity."""

    path: str | None
    language_hint: str | None
    operation: PreviewOperation
    hunks: tuple[_PreviewHunk, ...] = ()
    content: str | None = None
    start_line: int | None = None
    is_snippet: bool = False


def _json_value(value: str) -> object:
    """Parse JSON at one typed boundary."""
    parsed: object = json.loads(value)
    return parsed


def _python_literal(value: str) -> object:
    """Parse Gemini's documented Python-style argument mapping at one boundary."""
    parsed: object = ast.literal_eval(value)
    return parsed


def _mapping(value: object) -> dict[str, object] | None:
    """Return a string-keyed mapping from a mapping, JSON, or Python literal."""
    parsed: object = value
    if isinstance(value, str):
        if len(value) > _MAX_LITERAL_PARSE_CHARS:
            return None
        try:
            parsed = _json_value(value)
        except (TypeError, ValueError, MemoryError, RecursionError):
            try:
                parsed = _python_literal(value)
            except (SyntaxError, ValueError, MemoryError, RecursionError):
                return None
    if not isinstance(parsed, dict):
        return None
    return {
        key: item
        for key, item in cast("dict[object, object]", parsed).items()
        if isinstance(key, str)
    }


def _tool_info_parameters(mapping: dict[str, object]) -> dict[str, object] | None:
    """Return parameters nested under a ``tool_info`` envelope, when present.

    AGY's parser emits ``metadata = {"tool": ..., "tool_info": {"name": ...,
    "parameters": {...}}}`` instead of flattening parameters under ``input``.
    """
    tool_info = mapping.get("tool_info")
    if isinstance(tool_info, dict):
        return _mapping(tool_info.get("parameters"))
    return None


def _input(metadata: dict[str, object]) -> dict[str, object] | None:
    """Extract documented parser input/args envelopes without guessing keys.

    AGY's ``tool_info.parameters`` envelope is recognized both at the top level
    (a direct parser-real call) and when a caller wraps a raw AGY metadata
    envelope under ``input`` (the display preview path): an extracted mapping
    that itself carries a ``tool_info`` key is unwrapped to its parameters.
    """
    for key in ("input", "args", "arguments"):
        payload = _mapping(metadata.get(key))
        if payload is not None:
            nested = _tool_info_parameters(payload)
            return nested if nested is not None else payload
    return _tool_info_parameters(metadata)


def _path(payload: dict[str, object]) -> str | None:
    """Return a documented path field, if present."""
    for key in ("path", "file_path", "filePath", "filename", "TargetFile", "notebook_path"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _line(value: object) -> int | None:
    """Return a positive source line while rejecting bool."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _patch_hunks(patch: str) -> tuple[_PreviewHunk, ...]:
    """Extract unified-diff hunks with their new-file source line and label."""
    matches = list(_PATCH_HUNK_RE.finditer(patch))
    hunks: list[_PreviewHunk] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(patch)
        body = patch[match.end() : end]
        old = "\n".join(
            line[1:]
            for line in body.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        new = "\n".join(
            line[1:]
            for line in body.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        start = int(match.group("new"))
        hunks.append(_PreviewHunk(old, new, start, f"hunk {index + 1} (line {start})"))
    return tuple(hunks)


def _edit_hunks(payload: dict[str, object], *, multiple: bool) -> tuple[_PreviewHunk, ...]:
    """Normalize one replacement or the documented ordered ``edits`` list."""
    edits = payload.get("edits")
    items: tuple[object, ...] = (
        tuple(edits) if isinstance(edits, list) else (() if multiple else (payload,))
    )
    hunks: list[_PreviewHunk] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        old = (
            item.get("oldText")
            or item.get("old_string")
            or item.get("oldString")
            or ""
        )
        new = (
            item.get("newText")
            or item.get("new_string")
            or item.get("newString")
            or ""
        )
        hunks.append(
            _PreviewHunk(
                old if isinstance(old, str) else "",
                new if isinstance(new, str) else "",
                _line(item.get("start_line", item.get("line_start"))),
                f"edit {index}" if multiple else None,
            )
        )
    return tuple(hunks)


def _content_payload(
    operation: PreviewOperation, path: str | None, payload: dict[str, object]
) -> _PreviewPayload:
    content = payload.get("content")
    return _PreviewPayload(
        path,
        None,
        operation,
        content=content if isinstance(content, str) else None,
        start_line=_line(payload.get("line_start")),
        is_snippet=bool(payload.get("is_snippet", False)),
    )


def _notebook_payload(path: str | None, payload: dict[str, object]) -> _PreviewPayload:
    source = payload.get("new_source", payload.get("content", payload.get("source")))
    language = payload.get("kernel", payload.get("language"))
    hint = (
        language
        if isinstance(language, str)
        else ("python" if path and path.endswith(".ipynb") else None)
    )
    return _PreviewPayload(path, hint, "write", content=source if isinstance(source, str) else None)


def _patch_payload(path: str | None, payload: dict[str, object]) -> _PreviewPayload | None:
    patch = payload.get("patch", payload.get("input"))
    if not isinstance(patch, str):
        return None
    return _PreviewPayload(path, "diff", "patch", _patch_hunks(patch), patch)


PreviewHunk = _PreviewHunk
PreviewPayload = _PreviewPayload


def payload_from_tool_event(tool_name: str, metadata: dict[str, object]) -> PreviewPayload | None:
    """Normalize a recognized Ralph/native file event; otherwise return ``None``."""
    payload = _input(metadata)
    if payload is None:
        return None
    bare = (
        tool_name.removeprefix("ralph_mcp__ralph__")
        .removeprefix("mcp__ralph__")
        .removeprefix("ralph.")
    )
    path = _path(payload)
    operations: dict[str, PreviewOperation] = {
        "read_file": "read",
        "read": "read",
        "Read": "read",
        "read_multiple_files": "read",
        "grep_files": "read",
        "search_files": "read",
        "git_diff": "read",
        "git_show": "read",
        "git_log": "read",
        "exec": "read",
        "write_file": "write",
        "write": "write",
        "Write": "write",
        "append_file": "append",
        "append": "append",
        "Append": "append",
        "ralph_stage_md_artifact": "write",
        "ralph_submit_md_artifact": "write",
        "write_to_file": "write",
    }
    operation = operations.get(bare)
    if operation is not None:
        content = payload.get("content")
        return (
            _PreviewPayload(None, "diff", "read", content=content)
            if bare == "exec"
            and isinstance(content, str)
            and all(marker in content for marker in ("---", "+++", "@@"))
            else _content_payload(operation, path, payload)
            if bare != "exec"
            else None
        )
    if bare in {"edit_file", "edit", "Edit", "str_replace", "ralph_edit_md_artifact",
                "replace_file_content", "sed_file"}:
        return _PreviewPayload(path, None, "replace", _edit_hunks(payload, multiple=False))
    if bare in {"MultiEdit", "multi_replace_file_content"}:
        return _PreviewPayload(path, None, "replace", _edit_hunks(payload, multiple=True))
    if bare in {"NotebookEdit", "notebook_edit"}:
        return _notebook_payload(path, payload)
    return _patch_payload(path, payload) if bare == "apply_patch" else None


__all__ = ["PreviewHunk", "PreviewOperation", "PreviewPayload", "payload_from_tool_event"]
