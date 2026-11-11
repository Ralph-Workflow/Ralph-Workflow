
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final

from ralph.config.agent_transport import AgentTransport
from ralph.display._raw_log_break import RawLogBreak
from ralph.display.vt_normalizer import normalize_vt_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


TURN_BOUNDARY_MARKER: Final = "[claude turn boundary]"

PTY_EXIT_COMMAND: Final = "/exit"

COMPLETION_EVENT_LINE: Final = "[Completion event emitted to pipeline]"

HARNESS_PTY_INPUT_ECHO_LINES: frozenset[str] = frozenset(
    {TURN_BOUNDARY_MARKER, PTY_EXIT_COMMAND, COMPLETION_EVENT_LINE}
)


def is_harness_input_echo(line: str) -> bool:
    return line.strip() in HARNESS_PTY_INPUT_ECHO_LINES


def _is_canonical_transport_session_line(line: str) -> bool:
    from ralph.agents.invoke import is_whole_canonical_session_line

    return is_whole_canonical_session_line(normalize_vt_text(line).strip())


_INTERACTIVE_PTY_TRANSPORTS: frozenset[AgentTransport] = frozenset(
    {AgentTransport.CLAUDE_INTERACTIVE, AgentTransport.NANOCODER, AgentTransport.AGY}
)


def is_interactive_pty_transport(transport: AgentTransport | None) -> bool:
    return transport in _INTERACTIVE_PTY_TRANSPORTS


def detect_raw_log_breaks(
    raw_path: Path, *, transport: AgentTransport | None = None
) -> list[RawLogBreak]:
    breaks: list[RawLogBreak] = []
    if not raw_path.exists():
        return breaks
    try:
        payload = raw_path.read_bytes()
    except OSError as exc:
        return [
            RawLogBreak(
                kind="READ_ERROR",
                offset=0,
                detail=f"failed to read raw log: {exc}",
            )
        ]
    nul_offset = payload.find(b"\x00")
    if nul_offset >= 0:
        breaks.append(
            RawLogBreak(
                kind="NUL_BYTES",
                offset=nul_offset,
                detail=(
                    f"NUL-byte run begins at byte {nul_offset}; the "
                    "transcript is unparseable as JSONL past this point"
                ),
            )
        )
    if is_interactive_pty_transport(transport):
        return breaks
    return breaks + _detect_non_jsonl_breaks(payload)


MAX_REPORTED_BREAKS: Final = 32


_NUL_SKIP_WINDOW: Final = 1 << 16


def _skip_nul_run(payload: bytes, start: int) -> int:
    total = len(payload)
    while start < total:
        window = payload[start : start + _NUL_SKIP_WINDOW]
        stripped = window.lstrip(b"\x00")
        start += len(window) - len(stripped)
        if stripped:
            return start
    return total


def iter_capture_lines(chunk: bytes) -> Iterator[bytes]:
    start = 0
    total = len(chunk)
    next_newline = chunk.find(b"\n")
    next_carriage = chunk.find(b"\r")
    while start < total:
        if 0 <= next_newline < start:
            next_newline = chunk.find(b"\n", start)
        if 0 <= next_carriage < start:
            next_carriage = chunk.find(b"\r", start)
        if next_newline < 0 and next_carriage < 0:
            yield chunk[start:]
            return
        if next_carriage < 0 or (0 <= next_newline < next_carriage):
            end = next_newline
        else:
            end = next_carriage
            if chunk[end + 1 : end + 2] == b"\n":
                end += 1
        yield chunk[start : end + 1]
        start = end + 1


def nul_separated_chunks(payload: bytes) -> Iterator[tuple[int, bytes]]:
    start = 0
    total = len(payload)
    while start <= total:
        nul_at = payload.find(b"\x00", start)
        if nul_at < 0:
            yield start, payload[start:]
            return
        if nul_at > start:
            yield start, payload[start:nul_at]
        start = _skip_nul_run(payload, nul_at)


_MAX_LINE_INSPECT_BYTES: Final = 64 * 1024

_NON_PRINTABLE_ASCII: Final = re.compile(rb"[^\x20-\x7e]")

_MAX_CANONICAL_OPENING_BYTES: Final = 64

_UNSAFE_ID_RUN: Final = re.compile(r"[^0-9A-Za-z._-]+")


def _is_gradeable_without_normalising(line_bytes: bytes) -> bool:
    from ralph.agents.invoke import starts_with_canonical_session_marker

    if len(line_bytes) <= _MAX_LINE_INSPECT_BYTES:
        return False
    if _NON_PRINTABLE_ASCII.search(line_bytes) is not None:
        return False
    opening = line_bytes[:_MAX_CANONICAL_OPENING_BYTES].decode("ascii", errors="replace")
    return not starts_with_canonical_session_marker(opening)


def _non_jsonl_break(offset: int, line_bytes: bytes) -> RawLogBreak:
    quoted = line_bytes[:60].decode("utf-8", errors="replace")
    return RawLogBreak(
        kind="NON_JSONL",
        offset=offset,
        detail=(f"line at byte {offset} is not parseable JSON (first 60 chars: {quoted!r})"),
    )


def _parse_json_line(raw: bytes | str) -> tuple[object, bool]:
    try:
        parsed: object = json.loads(raw)
    except (ValueError, RecursionError):
        return None, False
    return parsed, True


def _grade_normalised_line(line_bytes: bytes, offset: int) -> RawLogBreak | None:
    line_text = normalize_vt_text(line_bytes.decode("utf-8", errors="replace").strip()).strip()
    if (
        not line_text
        or is_harness_input_echo(line_text)
        or _is_canonical_transport_session_line(line_text)
    ):
        return None
    parsed, parsed_ok = _parse_json_line(line_text)
    if not parsed_ok:
        return _non_jsonl_break(offset, line_text.encode("utf-8", errors="replace"))
    if isinstance(parsed, dict):
        return None
    return _non_object_break(offset, parsed)


def _non_object_break(offset: int, parsed: object) -> RawLogBreak:
    return RawLogBreak(
        kind="NON_JSONL",
        offset=offset,
        detail=(
            f"line at byte {offset} parses as JSON but "
            f"is not a JSON object (type={type(parsed).__name__})"
        ),
    )


def _grade_line(line_bytes: bytes, offset: int) -> RawLogBreak | None:
    parsed_raw, parsed_ok = _parse_json_line(line_bytes)
    if parsed_ok:
        if isinstance(parsed_raw, dict):
            return None
        return _non_object_break(offset, parsed_raw)
    if _is_gradeable_without_normalising(line_bytes):
        return _non_jsonl_break(offset, line_bytes)
    return _grade_normalised_line(line_bytes, offset)


def _detect_non_jsonl_breaks(payload: bytes) -> list[RawLogBreak]:
    breaks: list[RawLogBreak] = []
    for chunk_start, chunk in nul_separated_chunks(payload):
        if len(breaks) >= MAX_REPORTED_BREAKS:
            return breaks
        line_offset = chunk_start
        for raw_line in iter_capture_lines(chunk):
            if len(breaks) >= MAX_REPORTED_BREAKS:
                return breaks
            this_line_offset = line_offset
            line_offset += len(raw_line)
            line_bytes = raw_line.strip()
            if not line_bytes:
                continue
            found = _grade_line(line_bytes, this_line_offset)
            if found is not None:
                breaks.append(found)
    return breaks


__all__ = [
    "HARNESS_PTY_INPUT_ECHO_LINES",
    "MAX_REPORTED_BREAKS",
    "PTY_EXIT_COMMAND",
    "TURN_BOUNDARY_MARKER",
    "detect_raw_log_breaks",
    "is_harness_input_echo",
    "is_interactive_pty_transport",
    "iter_capture_lines",
    "nul_separated_chunks",
]
