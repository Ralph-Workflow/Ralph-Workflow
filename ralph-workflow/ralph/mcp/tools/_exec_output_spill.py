"""Shared oversized-output handling for the exec tool family.

When a command's formatted result is larger than the inline limit, the exec
tools must not dump it all into the agent's context (it floods the window) nor
discard it (the old bounded-exec behavior killed the process and raised, forcing
a blind retry loop that surfaced as ``-32001 Request timed out``). Instead the
full output is written to a temp file under the OS temp directory, and the agent
receives a bounded head/tail preview plus the path so it can read the rest in
chunks. The files are not deleted here; they are reclaimed by the OS's temp-dir
policy (e.g. ``systemd-tmpfiles`` / periodic cleanup / reboot), not immediately.

Both ``exec`` and ``unsafe_exec`` share these helpers so their output caps and
spill behavior cannot silently diverge.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ralph.mcp.tools.coordination import ToolContent, ToolResult

#: Above this many bytes the formatted result is spilled to a file instead of
#: inlined, so a large output cannot flood the agent's context window.
INLINE_OUTPUT_LIMIT_BYTES = 1 * 1024 * 1024
#: Hard cap on captured subprocess output for the bounded exec tool. The process
#: is killed past this; the captured tail is still spilled (not discarded).
SPILL_OUTPUT_LIMIT_BYTES = 10 * 1024 * 1024
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
    return Path(name)


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
) -> ToolResult:
    """Return the result inline, or spill to a file when it is too large.

    ``truncated`` forces a spill even under the inline limit, because a truncated
    capture means the full output never fit and the agent must be told so.
    """
    encoded_len = len(text.encode("utf-8", errors="replace"))
    is_error = returncode != 0
    if truncated or encoded_len > INLINE_OUTPUT_LIMIT_BYTES:
        spill_path = spill_output(text, spill_dir)
        preview = build_spill_preview(text, spill_path, encoded_len, truncated=truncated)
        return ToolResult(content=[ToolContent.text_content(preview)], is_error=is_error)
    return ToolResult(content=[ToolContent.text_content(text)], is_error=is_error)


__all__ = [
    "INLINE_OUTPUT_LIMIT_BYTES",
    "PREVIEW_HEAD_CHARS",
    "PREVIEW_TAIL_CHARS",
    "SPILL_OUTPUT_LIMIT_BYTES",
    "build_spill_preview",
    "format_or_spill",
    "spill_output",
]
