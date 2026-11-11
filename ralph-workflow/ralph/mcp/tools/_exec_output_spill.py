
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ralph.mcp.tools._cache_retention import prune_cache_files
from ralph.mcp.tools.coordination import ToolContent, ToolResult

INLINE_OUTPUT_LIMIT_BYTES = 1 * 1024 * 1024
SPILL_OUTPUT_LIMIT_BYTES = 10 * 1024 * 1024
SPILL_CACHE_MAX_TOTAL_BYTES = 64 * 1024 * 1024
PREVIEW_HEAD_CHARS = 8 * 1024
PREVIEW_TAIL_CHARS = 8 * 1024


def spill_output(text: str, spill_dir: Path | None) -> Path:
    directory = spill_dir if spill_dir is not None else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="ralph-exec-", suffix=".txt", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as spill_file:
            spill_file.write(text)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    path = Path(name)
    if spill_dir is not None:
        prune_cache_files(
            directory.glob("ralph-exec-*.txt"),
            max_total_bytes=SPILL_CACHE_MAX_TOTAL_BYTES,
            keep_paths=(path,),
        )
    return path


def build_spill_preview(text: str, path: Path, total_bytes: int, *, truncated: bool) -> str:
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
    exec_resource_resolver: object | None = None,
) -> ToolResult:
    encoded_len = len(text.encode("utf-8", errors="replace"))
    is_error = returncode != 0
    has_split_streams = stdout_text is not None or stderr_text is not None
    stdout_value = stdout_text if stdout_text is not None else text
    stderr_value = stderr_text if stderr_text is not None else ""
    stdout_encoded_len = len(stdout_value.encode("utf-8", errors="replace"))
    stderr_encoded_len = len(stderr_value.encode("utf-8", errors="replace"))

    def _register(spill: Path) -> str:
        if exec_resource_resolver is None:
            return f"ralph://exec/{spill.name}"
        register_method: object = getattr(exec_resource_resolver, "register", None)
        if not callable(register_method):
            return f"ralph://exec/{spill.name}"
        raw_result: object = register_method(spill)
        if isinstance(raw_result, str):
            return raw_result
        return f"ralph://exec/{spill.name}"

    if truncated or encoded_len > INLINE_OUTPUT_LIMIT_BYTES:
        spill_path = spill_output(text, spill_dir)
        preview = build_spill_preview(text, spill_path, encoded_len, truncated=truncated)
        if not summary:
            return ToolResult(content=[ToolContent.text_content(preview)], is_error=is_error)
        if has_split_streams:
            stdout_resource_id: str | None = None
            stdout_spill_path_v: str | None = None
            if stdout_value:
                stdout_spill = spill_output(stdout_value, spill_dir)
                stdout_resource_id = _register(stdout_spill)
                stdout_spill_path_v = str(stdout_spill)
            stderr_resource_id: str | None = None
            stderr_spill_path: str | None = None
            if stderr_value:
                stderr_spill = spill_output(stderr_value, spill_dir)
                stderr_resource_id = _register(stderr_spill)
                stderr_spill_path = str(stderr_spill)
        else:
            stdout_resource_id = _register(spill_path)
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
