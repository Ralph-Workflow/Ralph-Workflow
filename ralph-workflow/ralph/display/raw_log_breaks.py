"""Corruption grading for a raw agent-output capture.

Split out of :mod:`ralph.display.raw_overflow`, which owns WRITING a
capture. This module owns READING one back and deciding whether it is
damaged -- the verdict a phase reports as "raw transcript corrupted".

The two halves share only a file path, and keeping them in one module
had pushed it past the repo's size limit. The seam is also the honest
one: everything here is a pure function of bytes, and every guard in it
exists because a real capture was misgraded.
"""

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


#: Canonical harness-authored input lines that can appear verbatim in a
#: PTY transport's raw capture (measured live 2026-08-14, AGY smoke).
#: ``[claude turn boundary]`` is injected into the reader's line queue by
#: ``_pty_line_reader._request_interactive_exit`` / ``_sentinel_thread``
#: to delimit turns; ``/exit`` is typed into the agent's PTY stdin and
#: echoed back by the terminal line discipline. Both are Ralph-authored
#: harness input, not agent wire output — but they belong in the verbatim
#: capture, so the corruption detector must recognize them instead of
#: reporting a NON_JSONL break for every interactive-transport run.
TURN_BOUNDARY_MARKER: Final = "[claude turn boundary]"

#: The interactive-exit command the PTY line reader types into the agent
#: stdin at completion/stop time (echoed back into the capture).
PTY_EXIT_COMMAND: Final = "/exit"

#: Exact-match vocabulary of harness input lines tolerated by
#: :func:`detect_raw_log_breaks`. Exact match only: a line that merely
#: *contains* a marker is still graded, so an agent wire frame embedding
#: the marker text cannot smuggle a corrupted line past the detector.
HARNESS_PTY_INPUT_ECHO_LINES: frozenset[str] = frozenset(
    {TURN_BOUNDARY_MARKER, PTY_EXIT_COMMAND}
)


def is_harness_input_echo(line: str) -> bool:
    """Return True when ``line`` is a Ralph-authored harness input line.

    Exact (stripped) match against
    :data:`HARNESS_PTY_INPUT_ECHO_LINES`; never a substring test.
    """
    return line.strip() in HARNESS_PTY_INPUT_ECHO_LINES


def _is_canonical_transport_session_line(line: str) -> bool:
    """Return True when ``line`` is canonical PTY/session metadata.

    Interactive Claude emits human-readable session/resume/completion
    lines into the raw PTY capture (e.g. ``Session ID: <uuid>``).  They
    are not JSONL, but they are expected verbatim content -- not
    corruption.  ANSI/VT control codes are stripped before matching so
    TUI-styled banners are recognized.

    The canonical pattern vocabulary lives in
    ``ralph.agents.invoke._session`` and is imported here lazily to
    avoid an import-time cycle with ``ralph.agents.invoke`` (which
    imports this module for ``RawOverflowLog``).
    """
    from ralph.agents.invoke import is_whole_canonical_session_line

    # The STRICT predicate. The permissive one is for EXTRACTING an id
    # out of a decorated TUI line; using it here excused any line that
    # merely mentioned the completion sentence, so a frame severed
    # mid-write graded CLEAN whenever its surviving bytes contained
    # text the agent had echoed from a ``declare_complete`` result.
    return is_whole_canonical_session_line(normalize_vt_text(line).strip())


#: Transports whose raw capture is an interactive PTY stream rather than a
#: machine-readable JSONL protocol. For these transports visible tool
#: output, file contents, ANSI TUI redraws, and other human-readable text
#: are expected verbatim capture content, not corruption. NUL-byte
#: detection is still enforced (it signals the cross-writer truncation
#: hazard), but NON_JSONL grading is skipped so a healthy interactive run
#: is not mislabeled ``raw transcript corrupted``.
_INTERACTIVE_PTY_TRANSPORTS: frozenset[AgentTransport] = frozenset(
    {AgentTransport.CLAUDE_INTERACTIVE, AgentTransport.NANOCODER, AgentTransport.AGY}
)


def is_interactive_pty_transport(transport: AgentTransport | None) -> bool:
    """Return True when ``transport`` emits visible PTY output rather than JSONL."""
    return transport in _INTERACTIVE_PTY_TRANSPORTS



def detect_raw_log_breaks(
    raw_path: Path, *, transport: AgentTransport | None = None
) -> list[RawLogBreak]:
    """Read ``raw_path`` back as JSONL and return every corruption break.

    S-8 / C4 / DoD 15: a corrupted or truncated transcript is a reported
    break, not a silent skip. Two break shapes are detected:

    - ``NUL_BYTES``: any NUL byte anywhere in the file. The parser
      cannot recover the next JSON frame's start (it cannot tell where
      the JSON ends, since JSON itself permits ``\\u0000`` as an
      escaped sequence inside a string but a bare NUL cannot appear in
      a well-formed JSON document on the wire).
    - ``NON_JSONL``: a line that is not a parseable JSON object. This
      catches the shape where rendered text reaches the verbatim
      capture -- historically the display layer wrote its condensed
      bodies to this same path, which is why a rendered
      ``\u2713 PASS\u2026`` row or a markdown ``---`` front-matter line
      could appear mid-stream. The display now owns a separate file
      (see :data:`CONDENSED_LOG_SUFFIX`), so a break here means the
      agent's own output is damaged.

    For interactive PTY transports (``claude_interactive``, ``nanocoder``,
    ``agy``)
    the raw capture is expected human-visible output rather than JSONL,
    so only ``NUL_BYTES`` and ``READ_ERROR`` breaks are reported. Other
    streams (headless Claude, Codex, etc.) keep strict JSON-object
    validation for every line, with an allowlist for canonical
    session/resume/completion metadata lines emitted by the PTY/session
    layer.

    The exemption is keyed on the TRANSPORT, and there is exactly one
    ``AgentTransport.AGY`` -- so an AGY run in print mode is exempt too,
    despite emitting JSONL. This paragraph previously claimed the
    opposite ("AGY print mode ... keeps strict JSON-object validation"),
    which no code path did. Stated as it is rather than as it reads
    better: AGY print-mode captures are NOT graded for ``NON_JSONL``,
    and a corrupt frame there is reported by nothing. Splitting the
    transport is the fix if that matters; narrowing the exemption
    without splitting it would make every interactive AGY run report
    its ordinary human-visible output as corruption.

    The function reads the file in binary mode so a NUL-byte break is
    observable. ``read_text(errors='replace')`` would silently swallow
    the NUL bytes; the binary read keeps the byte-level fingerprint
    visible.

    An absent file returns an empty break list (no break observed, no
    break reported). A read error (locked file, missing parent) is
    reported as a break with detail naming the OSError so the operator
    sees the I/O failure rather than a silent empty result.
    """
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


#: Most breaks any one detection reports.
#:
#: Every caller uses ``breaks[0]`` -- the report names the first break
#: and its offset. Collecting the rest is pure cost, and on a badly
#: corrupted capture it is enormous: measured, a 10 MB file of short
#: non-JSON lines produced 5.2 million break objects in 119 s and 1.5 GB
#: of memory, at the 50 MB cap roughly ten minutes and 7 GB -- inside
#: the phase-close verdict, in exactly the case the detector exists for.
MAX_REPORTED_BREAKS: Final = 32


#: Window used to walk a NUL run at C speed rather than byte by byte.
_NUL_SKIP_WINDOW: Final = 1 << 16


def _skip_nul_run(payload: bytes, start: int) -> int:
    """Return the offset of the first non-NUL byte at or after ``start``.

    Walks in windows so the scan runs inside ``bytes.lstrip`` rather than
    a Python loop: a hole is millions of consecutive NULs, and stepping
    one byte at a time cost seconds on a capture at the file cap.
    """
    total = len(payload)
    while start < total:
        window = payload[start : start + _NUL_SKIP_WINDOW]
        stripped = window.lstrip(b"\x00")
        start += len(window) - len(stripped)
        if stripped:
            return start
    return total


def iter_capture_lines(chunk: bytes) -> Iterator[bytes]:
    r"""Yield ``chunk``'s lines lazily, keeping their terminators.

    ``bytes.splitlines`` materialises every line before the caller can
    stop, so the break cap could not bound it: 50 MB of short non-JSON
    lines allocated ~5.8 million objects and 356 MB before reporting its
    32 breaks -- inside the phase-close verdict, in the case the
    detector exists for.

    Splits on BOTH terminators, exactly as ``splitlines`` does. Matching
    only ``\n`` joined a bare-CR-separated line to its neighbour, and
    ``normalize_vt_text``'s carriage-return-overwrite semantics then
    erased the garbage before it could be graded -- so a corrupt line
    the old code reported went unseen. Measured on the field payload
    itself, one break became none: a perf change had silently blinded
    the detector this module exists to make trustworthy.
    """
    start = 0
    total = len(chunk)
    # Both terminator positions are CARRIED, not re-searched per line.
    # ``find`` scans to EOF when the byte is absent, and a healthy JSONL
    # capture contains no ``\r`` at all -- so searching for one on every
    # line made the scan O(lines x bytes). Measured on well-formed
    # stream-json frames: 2.9 s at 4 MB, 11.8 s at 8 MB, 38.1 s at 16 MB,
    # four times worse per doubling, extrapolating to minutes at the
    # 50 MB file cap. ``MAX_REPORTED_BREAKS`` cannot bound that, because
    # a healthy file has no breaks and the early return never fires --
    # and this runs on EVERY phase close, for every verdict label. A
    # carried position re-searches only after it is passed, so each byte
    # is visited once for each terminator.
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
            # CRLF is ONE terminator, not two.
            if chunk[end + 1 : end + 2] == b"\n":
                end += 1
        yield chunk[start : end + 1]
        start = end + 1


def nul_separated_chunks(payload: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield ``(offset, chunk)`` for each NUL-delimited run in ``payload``.

    Splits LAZILY. ``payload.split(b"\\x00")`` materialises one bytes
    object per NUL byte before any grading happens, so a NUL hole -- the
    measured 2026-08-06 corruption shape, and precisely what this
    detector exists to find -- cost 7.9 s and 517 MB at the 50 MB file
    cap, none of it bounded by :data:`MAX_REPORTED_BREAKS` because the
    allocation ran first. Yielding slices lets the caller stop at the
    cap.
    """
    start = 0
    total = len(payload)
    while start <= total:
        nul_at = payload.find(b"\x00", start)
        if nul_at < 0:
            yield start, payload[start:]
            return
        if nul_at > start:
            yield start, payload[start:nul_at]
        # Skip the whole NUL RUN, not one byte of it. A hole is millions
        # of consecutive NULs, and stepping through it one at a time
        # yielded one empty chunk per byte.
        start = _skip_nul_run(payload, nul_at)


#: Above this length, a non-JSON line of pure printable ASCII is graded
#: without the decode/normalise pass. Generous next to any marker that
#: pass matches (the longest is a few dozen bytes).
_MAX_LINE_INSPECT_BYTES: Final = 64 * 1024

#: Anything the VT normaliser could act on, or that could make the
#: normalised text differ from the raw bytes. Its absence is what makes
#: skipping the normalise pass safe.
_NON_PRINTABLE_ASCII: Final = re.compile(rb"[^\x20-\x7e]")

#: How much of a line has to be read to rule out a canonical
#: opening. Matches the bound the marker vocabulary itself uses.
_MAX_CANONICAL_OPENING_BYTES: Final = 64

#: Runs of anything that has no place in a filename component.
#: Collapsed to a single dash so two different flags cannot fold
#: onto one identity.
_UNSAFE_ID_RUN: Final = re.compile(r"[^0-9A-Za-z._-]+")


def _is_gradeable_without_normalising(line_bytes: bytes) -> bool:
    """True when the decode/normalise pass provably cannot change the verdict.

    Pure printable ASCII leaves the VT normaliser nothing to remove, and
    a line longer than the inspect cap cannot equal one of the short
    allowlisted markers -- so the answer is already known, and the pass
    would only cost several full-size copies of a multi-megabyte line.
    """
    from ralph.agents.invoke import starts_with_canonical_session_marker

    if len(line_bytes) <= _MAX_LINE_INSPECT_BYTES:
        return False
    if _NON_PRINTABLE_ASCII.search(line_bytes) is not None:
        return False
    # "No line longer than the cap can be one of the short allowlisted
    # markers" was WRONG, and it was the load-bearing half of the claim
    # that skipping is safe: the completion-marker branch matched a
    # marker as a SUBSTRING with an unanchored id search, so a canonical
    # line of any length was possible. The same text then graded clean
    # when short and corrupt when long -- the exact length-dependence
    # this guard was rewritten to remove.
    #
    # Both halves are fixed: the allowlist is anchored now, and a line
    # is only skipped when it does not OPEN with a canonical marker, so
    # the skip cannot decide the verdict for a line the allowlist would
    # have claimed.
    opening = line_bytes[:_MAX_CANONICAL_OPENING_BYTES].decode("ascii", errors="replace")
    return not starts_with_canonical_session_marker(opening)


def _non_jsonl_break(offset: int, line_bytes: bytes) -> RawLogBreak:
    """Build the break for a line that is not parseable JSON."""
    quoted = line_bytes[:60].decode("utf-8", errors="replace")
    return RawLogBreak(
        kind="NON_JSONL",
        offset=offset,
        detail=(f"line at byte {offset} is not parseable JSON (first 60 chars: {quoted!r})"),
    )


def _parse_json_line(raw: bytes | str) -> tuple[object, bool]:
    """Return ``(value, parsed)`` for one line of a capture.

    The cheapest discriminator, and the one that answers almost every
    line of a healthy capture. ``json.loads`` takes bytes directly, so a
    well-formed frame costs no decoded copy at all.

    Catches ``ValueError`` AND ``RecursionError``, not just
    ``JSONDecodeError``. Two ways a line of bytes takes a parser down:
    CPython caps integer literals at 4300 digits and raises a BARE
    ``ValueError`` past it, and deep nesting (a run of ``[``) exhausts
    the parser's stack and raises ``RecursionError``, which is not a
    ``ValueError`` at all. Either one propagated out of a grader two
    callers document as "never raises", taking the corruption verdict
    AND the phase's own verdict line with it -- silently, because both
    call sites swallow at DEBUG.

    A capture full of ``[`` is precisely what a truncated or interleaved
    write looks like, so the input that breaks the parser is the input
    the grader exists to judge. The recursion threshold is
    stack-dependent (~149,000 bytes on a main thread here, but between
    2,000 and 10,000 on a 512 KB thread), so it is not a shape that can
    be ruled out by size.
    """
    try:
        parsed: object = json.loads(raw)
    except (ValueError, RecursionError):
        return None, False
    return parsed, True


def _grade_normalised_line(line_bytes: bytes, offset: int) -> RawLogBreak | None:
    """Grade a line that did not parse as raw bytes, after VT normalisation.

    ``normalize_vt_text`` strips the VT/ANSI escapes an agent embeds in
    its own output, and the re-parse here is what rescues a frame that
    carries them. It needs the WHOLE line: truncating its input made the
    verdict depend on LENGTH, so the identical codex frame carrying a
    raw ESC byte graded clean at 160 bytes and "raw transcript
    corrupted" at 80 KB -- inventing the exact false verdict this
    detector exists to avoid, and quoting valid JSON as the proof.
    """
    line_text = normalize_vt_text(line_bytes.decode("utf-8", errors="replace").strip()).strip()
    if (
        not line_text
        # Ralph-authored harness input and canonical
        # session/resume/completion metadata are both expected verbatim
        # capture content, not corrupted or truncated frames.
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
    """Build the break for a line that is JSON but not a JSON object."""
    return RawLogBreak(
        kind="NON_JSONL",
        offset=offset,
        detail=(
            f"line at byte {offset} parses as JSON but "
            f"is not a JSON object (type={type(parsed).__name__})"
        ),
    )


def _grade_line(line_bytes: bytes, offset: int) -> RawLogBreak | None:
    """Return the break for one non-empty line, or ``None`` when it is fine."""
    parsed_raw, parsed_ok = _parse_json_line(line_bytes)
    if parsed_ok:
        if isinstance(parsed_raw, dict):
            return None
        # Reported for what it IS. The skip path below would otherwise
        # call a 70 KB JSON string "not parseable JSON", which is the
        # opposite of true and sends an operator looking for damage
        # that is not there.
        return _non_object_break(offset, parsed_raw)
    # Cost is bounded by SKIPPING the normalise pass rather than
    # shortening its input; see :func:`_is_gradeable_without_normalising`
    # for why that cannot change the answer.
    if _is_gradeable_without_normalising(line_bytes):
        return _non_jsonl_break(offset, line_bytes)
    return _grade_normalised_line(line_bytes, offset)


def _detect_non_jsonl_breaks(payload: bytes) -> list[RawLogBreak]:
    """Return one ``NON_JSONL`` break per unparseable line.

    Splits the payload on NUL bytes first so a measured NUL-hole run
    does not silently swallow rendered text that follows the hole on
    the same line. Each chunk between NUL runs is then parsed as
    JSONL: lines that parse as JSON objects are skipped, and every
    other non-empty line (rendered ``\u2713 PASS\u2026`` text, control
    codes, malformed JSON) is a break.

    Canonical interactive-transport session/resume/completion metadata
    lines (see :func:`_is_canonical_transport_session_line`) are expected
    verbatim capture content, not corruption. They are recognized after
    VT/ANSI normalization so TUI-wrapped session lines are not misgraded.
    """
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
            # ONE copy, and no decode on the healthy path. Each of
            # ``rstrip`` / ``rstrip`` / ``decode`` / ``strip`` allocated
            # a full-size copy of the line before anything was graded,
            # so a single long frame cost roughly twenty times its own
            # size -- 490 MB transient on a 24 MB line, and 24 MB single
            # frames are measured real (see AGENT_STREAM_BUFFER_BYTES).
            # ``MAX_REPORTED_BREAKS`` bounds the NUMBER of lines graded,
            # never the cost of grading one.
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
