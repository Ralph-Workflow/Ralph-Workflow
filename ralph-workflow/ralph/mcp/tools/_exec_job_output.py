"""Output formatting, buffering, and serialization for exec jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools._exec_output_spill import (
    INLINE_OUTPUT_LIMIT_BYTES,
    build_spill_preview,
    spill_output,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager._liveness_assessor import LivenessVerdict


def serialize_liveness_verdict(verdict: LivenessVerdict | None) -> dict[str, object] | None:
    """Serialize full liveness verdict payload including raw signals."""
    if verdict is None:
        return None
    return {
        "score": verdict.score,
        "pressure": verdict.pressure,
        "stall_window_s": verdict.stall_window_s,
        "strongest_rule": verdict.strongest_rule,
        "suspicious": verdict.suspicious,
        "verdict_kind": verdict.verdict_kind,
        "signals": verdict.signals,
        "elapsed_s": verdict.elapsed_s,
    }


def _append_tail_buffer(
    head: bytearray, tail: bytearray, chunk: bytes, max_bytes: int = 65_536
) -> None:
    """Retain bounded, non-overlapping head and tail buffers of raw bytes.

    The head keeps the first ``max_bytes`` of the stream; only bytes past the
    head feed the tail, which keeps the last ``max_bytes`` of those. A stream
    that fits in both is therefore captured exactly once.
    """
    head_capacity = max(0, max_bytes - len(head))
    head.extend(chunk[:head_capacity])
    tail.extend(chunk[head_capacity:])
    if len(tail) > max_bytes:
        del tail[:-max_bytes]


def _merge_stream_buffers(head: bytearray, tail: bytearray, total_bytes: int) -> tuple[bytes, bool]:
    """Join a stream's head/tail buffers; return the payload and whether bytes were dropped.

    The truncation marker separates head and tail only when the stream was
    longer than both together, i.e. when bytes between them were discarded.
    """
    if total_bytes > len(head) + len(tail):
        return bytes(head) + b"\n... [truncated] ...\n" + bytes(tail), True
    return bytes(head) + bytes(tail), False


def _format_job_output(
    stdout_bytes: bytes,
    stderr_bytes: bytes,
    *,
    total_stdout_len: int,
    total_stderr_len: int,
    truncated: bool,
    spill_dir: Path | None = None,
) -> dict[str, object]:
    """Format captured job output according to standard exec preview/spill shape.

    ``truncated`` reports whether either stream dropped bytes between its
    head and tail capture buffers.
    """
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    combined_len = total_stdout_len + total_stderr_len

    preview_text = ""
    spill_path: str | None = None

    if combined_len > INLINE_OUTPUT_LIMIT_BYTES or truncated:
        combined_text = (
            f"Stdout:\n{stdout_text}\nStderr:\n{stderr_text}" if stderr_text else stdout_text
        )
        path = spill_output(combined_text, spill_dir)
        spill_path = str(path)
        preview_text = build_spill_preview(combined_text, path, combined_len, truncated=truncated)
    else:
        preview_parts: list[str] = []
        if stdout_text:
            preview_parts.append(f"Stdout:\n{stdout_text}")
        if stderr_text:
            preview_parts.append(f"Stderr:\n{stderr_text}")
        preview_text = "\n\n".join(preview_parts)

    return {
        "stdout": stdout_text,
        "stderr": stderr_text,
        "stdout_bytes": total_stdout_len,
        "stderr_bytes": total_stderr_len,
        "truncated": truncated,
        "spill_path": spill_path,
        "preview": preview_text,
    }
