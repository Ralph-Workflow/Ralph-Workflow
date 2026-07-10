"""Shared oversized-output handling for the exec tool family.

When a command's formatted result is larger than the inline limit, the exec
tools must not dump it all into the agent's context (it floods the window) nor
discard it (the old bounded-exec behavior killed the process and raised, forcing
a blind retry loop that surfaced as ``-32001 Request timed out``). Instead the
full output is written to a spill file and the agent receives a bounded head/tail
preview plus the path so it can read the rest in chunks. The exec tools pass a
workspace-relative ``spill_dir`` (``<workspace>/.agent/tmp``) so the spill is
reachable by the agent's workspace-scoped read tools — a spill outside the
workspace root is rejected by those tools, leaving the agent told to read a file
it cannot open. ``spill_output`` falls back to the OS temp dir only when no
directory is supplied (direct/library callers). The files are not deleted here.

Both ``exec`` and ``unsafe_exec`` share these helpers so their output caps and
spill behavior cannot silently diverge.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ralph.mcp.tools._cache_retention import prune_cache_files
from ralph.mcp.tools.coordination import ToolContent, ToolResult

#: Above this many bytes the formatted result is spilled to a file instead of
#: inlined, so a large output cannot flood the agent's context window.
INLINE_OUTPUT_LIMIT_BYTES = 1 * 1024 * 1024
#: Hard cap on captured subprocess output for the bounded exec tool. The process
#: is killed past this; the captured tail is still spilled (not discarded).
SPILL_OUTPUT_LIMIT_BYTES = 10 * 1024 * 1024
#: Total retained exec spill cache under a workspace's .agent/tmp directory.
SPILL_CACHE_MAX_TOTAL_BYTES = 64 * 1024 * 1024
#: Head/tail sizes (chars) for the inline preview shown alongside a spill path.
PREVIEW_HEAD_CHARS = 8 * 1024
PREVIEW_TAIL_CHARS = 8 * 1024


def spill_output(text: str, spill_dir: Path | None) -> Path:
    """Write the full formatted output to a temp file and return its path.

    Defaults to the OS temp directory so the OS reclaims the file; ``spill_dir``
    overrides this (used in tests and configurable deployments).
    """
    directory = spill_dir if spill_dir is not None else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="ralph-exec-", suffix=".txt", dir=str(directory))
    with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as spill_file:
        spill_file.write(text)
    path = Path(name)
    if spill_dir is not None:
        prune_cache_files(
            directory.glob("ralph-exec-*.txt"),
            max_total_bytes=SPILL_CACHE_MAX_TOTAL_BYTES,
            keep_paths=(path,),
        )
    return path


def build_spill_preview(text: str, path: Path, total_bytes: int, *, truncated: bool) -> str:
    """Render a bounded head/tail preview pointing at the full spilled output."""
    truncation_note = " (truncated at the capture cap)" if truncated else ""
    head = text[:PREVIEW_HEAD_CHARS]
    tail = text[-PREVIEW_TAIL_CHARS:]
    return (
        f"Output was {total_bytes} bytes{truncation_note} — too large to inline. "
        f"Full output written to: {path}\n"
        f"Read it with the read tools or exec, e.g. `sed -n '1,200p' {path}`.\n\n"
        f"--- HEAD (first {PREVIEW_HEAD_CHARS} chars) ---\n{head}\n"
        f"--- TAIL (last {PREVIEW_TAIL_CHARS} chars) ---\n{tail}"
    )


def format_or_spill(
    text: str,
    *,
    returncode: int,
    truncated: bool,
    spill_dir: Path | None,
    summary: bool = False,
    stdout_text: str | None = None,
    stderr_text: str | None = None,
) -> ToolResult:
    """Return the result inline, or spill to a file when it is too large.

    ``truncated`` forces a spill even under the inline limit, because a truncated
    capture means the full output never fit and the agent must be told so.

    AC-11 contract: when ``summary=True`` the response is a JSON
    envelope carrying ``stdout_resource_id`` / ``stderr_resource_id``
    replayable handles plus spill paths and a head/tail preview. The
    raw output fallback (``summary=False``) is the legacy behavior.

    ``stdout_text`` and ``stderr_text`` are optional pre-split streams;
    when supplied they are tracked separately so the stdout/stderr
    resource ids point at per-stream spill files instead of the
    combined text. When omitted, ``text`` is treated as stdout (the
    pre-AC-11 contract).
    """
    encoded_len = len(text.encode("utf-8", errors="replace"))
    is_error = returncode != 0
    has_split_streams = stdout_text is not None or stderr_text is not None
    stdout_value = stdout_text if stdout_text is not None else text
    stderr_value = stderr_text if stderr_text is not None else ""
    stdout_encoded_len = len(stdout_value.encode("utf-8", errors="replace"))
    stderr_encoded_len = len(stderr_value.encode("utf-8", errors="replace"))
    stdout_truncated = stdout_encoded_len > INLINE_OUTPUT_LIMIT_BYTES
    stderr_truncated = stderr_encoded_len > INLINE_OUTPUT_LIMIT_BYTES
    if truncated or encoded_len > INLINE_OUTPUT_LIMIT_BYTES:
        # Spill the combined text so the legacy fallback (summary=False)
        # keeps working; for summary=True we also spill each stream
        # separately when the caller provided split streams.
        spill_path = spill_output(text, spill_dir)
        preview = build_spill_preview(text, spill_path, encoded_len, truncated=truncated)
        if not summary:
            return ToolResult(
                content=[ToolContent.text_content(preview)], is_error=is_error
            )
        # AC-11: when the caller provided split streams, each stream
        # gets its own resource id and spill path so the agent can
        # re-read stdout and stderr independently. When the caller
        # did not split, the combined spill is the single source of
        # truth and we report it under stdout_resource_id.
        if has_split_streams:
            stdout_resource_id: str | None = None
            stdout_spill_path_v: str | None = None
            if stdout_value and (stdout_truncated or stdout_value):
                stdout_spill = spill_output(stdout_value, spill_dir)
                stdout_resource_id = f"ralph://exec/{stdout_spill.name}"
                stdout_spill_path_v = str(stdout_spill)
            stderr_resource_id: str | None = None
            stderr_spill_path: str | None = None
            if stderr_value and stderr_truncated:
                stderr_spill = spill_output(stderr_value, spill_dir)
                stderr_resource_id = f"ralph://exec/{stderr_spill.name}"
                stderr_spill_path = str(stderr_spill)
        else:
            stdout_resource_id = f"ralph://exec/{spill_path.name}"
            stdout_spill_path_v = str(spill_path)
            stderr_resource_id = None
            stderr_spill_path = None
        payload = {
            "format": "summary",
            "returncode": returncode,
            "is_error": is_error,
            "stdout_bytes": stdout_encoded_len,
            "stderr_bytes": stderr_encoded_len,
            "truncated": truncated,
            "stdout_resource_id": stdout_resource_id,
            "stdout_spill_path": stdout_spill_path_v,
            "stderr_resource_id": stderr_resource_id,
            "stderr_spill_path": stderr_spill_path,
            "preview": preview,
        }

        return ToolResult(
            content=[ToolContent.text_content(json.dumps(payload))],
            is_error=is_error,
        )
    if not summary:
        return ToolResult(content=[ToolContent.text_content(text)], is_error=is_error)
    payload = {
        "format": "summary",
        "returncode": returncode,
        "is_error": is_error,
        "stdout_bytes": stdout_encoded_len,
        "stderr_bytes": stderr_encoded_len,
        "truncated": False,
        "stdout_resource_id": None,
        "stdout_spill_path": None,
        "stderr_resource_id": None,
        "stderr_spill_path": None,
        "stdout": stdout_value,
        "stderr": stderr_value,
    }

    return ToolResult(
        content=[ToolContent.text_content(json.dumps(payload))],
        is_error=is_error,
    )


__all__ = [
    "INLINE_OUTPUT_LIMIT_BYTES",
    "PREVIEW_HEAD_CHARS",
    "PREVIEW_TAIL_CHARS",
    "SPILL_CACHE_MAX_TOTAL_BYTES",
    "SPILL_OUTPUT_LIMIT_BYTES",
    "build_spill_preview",
    "format_or_spill",
    "spill_output",
]
