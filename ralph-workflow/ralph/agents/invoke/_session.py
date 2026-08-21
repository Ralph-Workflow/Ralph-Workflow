"""Session ID extraction and bounded output utilities."""

from __future__ import annotations

import json
import re
from typing import cast

from ralph.agents._session_markers import TURN_BOUNDARY_MARKER
from ralph.agents.invoke._pty_helpers import _visible_tui_text

#: Explicit completion marker emitted by the interactive harness.
#: Lines carrying this marker also contain a session id extracted by
#: the PTY/session layer; they are canonical completion metadata, not
#: corrupted wire output.
_EXPLICIT_COMPLETION_MARKER: str = "Task declared complete:"

#: Canonical interactive-transport session/resume metadata lines.
#:
#: These exact text shapes are emitted and parsed by the PTY/session
#: layer for interactive Claude and related transports. They are not
#: JSONL, but they are expected verbatim capture content -- not
#: corruption -- so consumers that grade raw transcripts must recognize
#: them. Anchored patterns only: a line that merely mentions
#: ``Session ID`` without matching a canonical shape is still graded as
#: ``NON_JSONL``.
_TRANSPORT_SESSION_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^Claude session ready\. Session ID:\s*([A-Za-z0-9._:-]+)$"),
    re.compile(r"^Session ID:\s*([A-Za-z0-9._:-]+)$", re.IGNORECASE),
    re.compile(r"^Resume this session with --resume\s+([A-Za-z0-9._:-]+)$"),
    re.compile(r"^--resume\s+([A-Za-z0-9._:-]+)$"),
    re.compile(r"^--session\s+([A-Za-z0-9._:-]+)$"),
)

#: Patterns that extract a session id from an explicit completion
#: marker line. These are anchored loosely inside the marker text so
#: future timestamp/summary ordering changes do not silently break
#: recognition.
_COMPLETION_SESSION_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"session_id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"sessionId\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
)


def extract_transport_text_session_id(stripped: str) -> str | None:
    """Extract a session id from a canonical transport text session line.

    Single source of truth for the exact text shapes the PTY/session
    layer emits and parses. Callers that need ANSI/VT-normalized
    matching (e.g. the raw-log classifier) normalize the line first,
    then call this helper.
    """
    if _EXPLICIT_COMPLETION_MARKER in stripped:
        for pattern in _COMPLETION_SESSION_ID_PATTERNS:
            match = pattern.search(stripped)
            if match is not None:
                return match.group(1)
    for pattern in _TRANSPORT_SESSION_TEXT_PATTERNS:
        match = pattern.search(stripped)
        if match is not None:
            return match.group(1)
    return None


#: The HEAD of an emitted completion line: the marker and a session id.
#: Anchored at the start, with a bounded id, so matching cannot backtrack.
_COMPLETION_HEAD_PATTERN: re.Pattern[str] = re.compile(
    r"^Task declared complete:\s*session_?id\s*[:=]\s*[A-Za-z0-9._:-]+",
    re.IGNORECASE,
)

#: The TAIL: ``timestamp=<int>`` closing the line, and nothing after it.
#: ``coordination.py`` interpolates ``now_fn() -> int``, so digits are the
#: whole vocabulary. A looser class let a concatenated tail ride along:
#: ``timestamp=1699999999abc`` satisfied ``[0-9A-Za-z._:+-]+$``.
_COMPLETION_TAIL_PATTERN: re.Pattern[str] = re.compile(
    r"timestamp\s*[:=]\s*\d+$",
    re.IGNORECASE,
)

#: A timestamp FIELD, not the bare word: an agent summary may mention
#: "timestamp" in prose without it being one. Matched over the ORIGINAL
#: string so the offsets are usable -- ``str.lower()`` is not
#: length-preserving (U+0130 lowers to two code points), so taking an
#: index from a lowered copy and applying it to the original shifted the
#: tail match by one per such character and reported a genuine line as
#: corrupt.
_TIMESTAMP_FIELD_PATTERN: re.Pattern[str] = re.compile(r"\btimestamp\s*[:=]", re.IGNORECASE)

#: The literal openings of every canonical session/completion line.
#:
#: Derived from the same vocabulary as the patterns above, and used for
#: two things that both need to be CHEAP: deciding whether a line could
#: possibly be canonical before matching it, and letting a grader skip
#: expensive work on a line that provably is not.
_CANONICAL_LINE_OPENINGS: tuple[str, ...] = (
    "claude session ready.",
    "session id:",
    "resume this session with --resume",
    "--resume",
    "--session",
    _EXPLICIT_COMPLETION_MARKER.lower(),
)

#: Longest opening above, plus room for leading whitespace. Bounds how
#: much of a line has to be examined to rule it out.
_MAX_CANONICAL_OPENING_CHARS = 64


def starts_with_canonical_session_marker(stripped: str) -> bool:
    """Return True when ``stripped`` OPENS with a canonical marker.

    A cheap necessary condition, not a sufficient one. A grader uses it
    to decide whether a line is worth examining closely; a line that
    fails it cannot be canonical however long it is.
    """
    opening = stripped[:_MAX_CANONICAL_OPENING_CHARS].lower()
    return any(opening.startswith(marker) for marker in _CANONICAL_LINE_OPENINGS)


def is_whole_canonical_session_line(stripped: str) -> bool:
    """Return True when the WHOLE line is canonical session metadata.

    Strict counterpart to :func:`is_canonical_session_text_line`. The two
    answer different questions and a grader needs this one.

    Extraction is deliberately permissive: it must find a session id
    inside a TUI-wrapped, decorated line, so it tests the completion
    marker as a SUBSTRING and searches for the id UNANCHORED. Reusing it
    to decide "is this line expected content" therefore excused any line
    that merely mentioned the marker text -- and agents routinely echo a
    ``declare_complete`` result into their own prose. A frame severed
    mid-write was graded CLEAN whenever the surviving bytes happened to
    contain that sentence, which is precisely the corruption the grader
    exists to catch.

    Anchored at BOTH ends. Anchoring only the front left the mirror
    image: a line opening with the marker was excused however it ended,
    so ``Task declared complete: session_id=1`` followed by a whole
    concatenated frame -- the classic lost-newline interleave -- still
    graded clean. The emitted line always closes with ``timestamp=``
    (``mcp/tools/coordination.py``), so requiring that closes the tail
    without constraining the free-text summary between them.
    """
    for pattern in _TRANSPORT_SESSION_TEXT_PATTERNS:
        if pattern.match(stripped) is not None:
            return True
    return _is_whole_completion_line(stripped)


def _is_whole_completion_line(stripped: str) -> bool:
    """Match a completion line head-then-tail, without backtracking.

    Two separate anchored matches joined by ``rfind``, NOT one pattern.
    Writing it as ``^head.*\btimestamp...$`` made the ``.*`` backtrack
    across the whole line looking for the tail: 28.8 seconds to grade a
    single 508 KB line, inside the phase-close verdict, with no timeout
    and a caller documented as never raising. Head match, tail search,
    tail match: each bounded, the whole thing linear.

    The tail's value is a bounded character class rather than ``\\S+``.
    ``\\S+`` swallowed a concatenated wire frame whenever that frame
    happened to contain no whitespace -- and real frames are compact --
    so the very interleave this anchor was added to catch graded CLEAN
    or CORRUPT depending on whether the lost frame had a space in it.
    """
    head = _COMPLETION_HEAD_PATTERN.match(stripped)
    if head is None:
        return False
    # The FIRST timestamp field, not the last. ``rfind`` walked to the
    # last one and left everything before it unconstrained, so a
    # completion line followed by a coordination line -- both emitted by
    # ``mcp/tools/coordination.py``, both ending in a timestamp -- graded
    # CLEAN. Anchoring on the first, and requiring digits to end of line,
    # means anything between the two is what fails the match.
    marker = _TIMESTAMP_FIELD_PATTERN.search(stripped)
    if marker is None or marker.start() < head.end():
        return False
    return _COMPLETION_TAIL_PATTERN.match(stripped, marker.start()) is not None


def is_canonical_session_text_line(stripped: str) -> bool:
    """Return True when ``stripped`` is a canonical transport session/completion line.

    Single source of truth for raw-log graders and other consumers that
    need to recognize session/resume/completion metadata without
    reimplementing the pattern vocabulary.
    """
    return extract_transport_text_session_id(stripped) is not None


_TRANSPORT_JSON_TYPES = frozenset(
    {
        "session",
        "session_ready",
        "session_start",
        "session_resume",
    }
)


def _is_cursor_system_init_event(parsed: dict[str, object]) -> bool:
    return parsed.get("type") == "system" and parsed.get("subtype") == "init"


def _is_kimi_meta_session_frame(parsed: dict[str, object]) -> bool:
    """Match Kimi Code's role-keyed ``session.resume_hint`` meta frame.

    Kimi Code's NDJSON frames are keyed by ``role`` rather than ``type``;
    the session-bearing frame observed on the live v0.36.x wire is
    ``{"role": "meta", "type": "session.resume_hint", "session_id": ...}``
    (emitted right after session start, alongside a ``system.version``
    meta frame that carries no session id).  Requiring BOTH the ``meta``
    role and the ``session.resume_hint`` type keeps assistant/tool
    frames (whose free-text content could otherwise collide with the
    generic ``session_id`` keys) from masquerading as transport
    session metadata.
    """
    return parsed.get("role") == "meta" and parsed.get("type") == "session.resume_hint"



def _match_transport_json_session_id(parsed: dict[str, object]) -> str | None:
    event_type = parsed.get("type")
    is_agy_init = parsed.get("event") == "init"
    if (
        (isinstance(event_type, str) and event_type in _TRANSPORT_JSON_TYPES)
        or _is_cursor_system_init_event(parsed)
        or is_agy_init
        or _is_kimi_meta_session_frame(parsed)
    ):
        for key in ("session_id", "sessionId", "conversation_id", "id"):
            session_id = parsed.get(key)
            if isinstance(session_id, str) and session_id:
                return session_id
    # OpenCode never emits a dedicated session frame: it stamps ``sessionID``
    # (capital I, capital D -- distinct from the ``sessionId`` above) on EVERY
    # event. Gating that spelling behind ``_TRANSPORT_JSON_TYPES`` meant the
    # session was never captured for OpenCode, and the smoke reported "session
    # ID was not observed" for a run whose every line carried one. The casing is
    # OpenCode-specific, so accepting it unconditionally cannot shadow another
    # transport's session key.
    opencode_session_id = parsed.get("sessionID")
    if isinstance(opencode_session_id, str) and opencode_session_id:
        return opencode_session_id
    return _session_id_from_nested_meta(parsed)


def _session_id_from_nested_meta(parsed: dict[str, object]) -> str | None:
    """Pull a session id out of a nested ``meta`` object."""
    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        return None
    for key in ("session_id", "sessionId"):
        session_id = meta.get(key)
        if isinstance(session_id, str) and session_id:
            return session_id
    return None


def _extract_transport_session_id_from_line(line: str) -> str | None:
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        # The primary line extractor matches canonical text patterns on
        # the raw line. ANSI/VT stripping is handled by
        # ``extract_transport_session_id_with_visible_tui`` so that
        # callers which need the original wire bytes (e.g. subprocess
        # readers that parse NDJSON frames) are not silently normalized.
        return extract_transport_text_session_id(line.strip())
    if not isinstance(parsed, dict):
        return None
    return _match_transport_json_session_id(parsed)


def extract_transport_session_id(raw_output: list[str] | tuple[str, ...]) -> str | None:
    """Extract only top-level transport/runtime session IDs from output lines."""
    for line in raw_output:
        session_id = _extract_transport_session_id_from_line(line)
        if session_id:
            return session_id
    return None


def extract_transport_session_id_from_line(line: str) -> str | None:
    """Extract only top-level transport/runtime session IDs from one line."""
    return _extract_transport_session_id_from_line(line)


def extract_transport_session_id_with_visible_tui(line: str) -> str | None:
    """Extract transport session IDs from a PTY line, with ANSI-strip fallback.

    PTY output lines frequently contain ANSI escape codes that prevent
    ``extract_transport_session_id_from_line`` from matching the
    anchored text patterns in :data:`_TRANSPORT_TEXT_SESSION_PATTERNS`
    (e.g. ``^Claude session ready\\. Session ID:\\s*(...)$``). The
    visible-TUI helper :func:`extract_visible_tui_transport_session_id`
    strips ANSI codes via :func:`ralph.agents.invoke._pty_helpers._visible_tui_text`
    before matching, so a TUI line like
    ``\\x1b[32mClaude session ready. Session ID: abc123\\x1b[0m``
    still yields the captured id.

    Used by the PTY watchdog / recovery paths so the resumable
    session id survives a watchdog-kill -> resume flow on the PTY
    transport. Mirrors the per-line capture already used by
    :meth:`PtyLineReader._record_transcript_session_id`.
    """
    primary = extract_transport_session_id_from_line(line)
    if primary:
        return primary
    # Fallback: strip ANSI/VT control codes and re-run the text-pattern
    # extractor so a session id carried in a TUI banner or status line
    # is captured even when the wire line starts with escape sequences.
    visible_line = _visible_tui_text(line)
    if visible_line and visible_line != line.strip():
        return extract_transport_text_session_id(visible_line)
    return None


def extract_visible_tui_transport_session_id(text: str) -> str | None:
    """Extract transport session IDs from visible TUI text only.

    This intentionally excludes generic ``session_id=...`` patterns so assistant or
    tool text cannot masquerade as transport session metadata.
    """
    return extract_transport_text_session_id(text.strip())


def _bounded_output_lines(
    raw_output: list[str] | tuple[str, ...],
    *,
    explicit_completion_seen: bool = False,
) -> list[str]:
    lines = list(raw_output)
    if explicit_completion_seen and not any(_EXPLICIT_COMPLETION_MARKER in line for line in lines):
        lines.append(_EXPLICIT_COMPLETION_MARKER)
    return lines


__all__ = [
    "TURN_BOUNDARY_MARKER",
    "_EXPLICIT_COMPLETION_MARKER",
    "_bounded_output_lines",
    "extract_transport_session_id",
    "extract_transport_session_id_from_line",
    "extract_transport_session_id_with_visible_tui",
    "extract_visible_tui_transport_session_id",
]
