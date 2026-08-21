"""The corruption detector must not go blind on a bare carriage return.

``detect_raw_log_breaks`` grades the verbatim raw capture and its verdict
is what a phase reports as "raw transcript corrupted". It used to reach
its lines through ``bytes.splitlines(keepends=True)``. That was replaced
with a hand-rolled scan so the break cap could bound the work -- 50 MB of
short non-JSON lines had allocated ~5.8 million objects before reporting
its 32 breaks, inside the phase-close verdict.

The replacement matched only ``\n``. ``splitlines`` also splits on ``\r``,
so a bare-CR-separated corrupt line was handed to the grader glued to its
neighbour, and ``normalize_vt_text``'s carriage-return-overwrite
semantics erased the garbage before it could be judged. The break
disappeared. These tests pin the scan against the oracle it replaced, so
a future optimisation cannot quietly narrow what counts as a line.
"""

from __future__ import annotations

import json
from pathlib import Path

from ralph.config.enums import AgentTransport
from ralph.display.raw_log_breaks import detect_raw_log_breaks, iter_capture_lines

# The exact payload shape from the field incident: a codex frame, a bare
# carriage return, then the next frame. Nothing here contains a newline
# between the garbage and the frame that follows it.
_FIELD_PAYLOAD = b'{"type":"item.started"}\n--- \r{"type":"item.completed"}\n'


def _breaks(tmp_path: Path, payload: bytes) -> list[tuple[str, int]]:
    log = tmp_path / "raw.log"
    log.write_bytes(payload)
    return [
        (break_.kind, break_.offset)
        for break_ in detect_raw_log_breaks(log, transport=AgentTransport.CODEX)
    ]


def test_a_bare_carriage_return_still_separates_a_corrupt_line(tmp_path: Path) -> None:
    """The field payload must grade as corrupt, not as clean."""
    assert _breaks(tmp_path, _FIELD_PAYLOAD) == [("NON_JSONL", 24)]


def test_crlf_is_one_terminator_and_not_a_break(tmp_path: Path) -> None:
    """Windows-style framing is well-formed JSONL, not corruption.

    Asserted on ``iter_capture_lines`` directly, not only through the
    verdict. The docstring here used to claim that counting ``\r`` and
    ``\n`` separately would "report a clean file as corrupt" -- it would
    not: the extra empty line is dropped before grading and the byte
    offsets are identical, so disabling the CRLF join changed no verdict
    and the test passed for a reason it did not state. The line
    boundaries are the thing this guard actually decides, and the
    contract is that they match ``bytes.splitlines``.
    """
    payload = b'{"type":"a"}\r\n{"type":"b"}\r\n{"type":"c"}\r\n'

    assert _breaks(tmp_path, payload) == []
    assert list(iter_capture_lines(payload)) == payload.splitlines(keepends=True)
    # CRLF is ONE terminator: three frames, three lines, no empty one.
    assert len(list(iter_capture_lines(payload))) == 3


def test_the_scan_agrees_with_the_splitlines_oracle_it_replaced(tmp_path: Path) -> None:
    """Differential test: same lines, same offsets, as ``splitlines``.

    Both the line CONTENT and the byte OFFSET matter -- the offset is
    quoted verbatim in the operator-facing verdict, so an off-by-one in
    the terminator arithmetic sends them to the wrong byte of a
    multi-megabyte file.

    The payload deliberately excludes lines the detector exempts by
    POLICY (the reader's own ``Session ID:`` markers, which are not
    agent output and are not corruption). Mixing those in would make
    this assert the exemption list rather than the framing, and the
    framing is what changed.
    """
    fragments = [
        b'{"type":"item.started"}',
        b"--- ",
        b"\xe2\x9c\x93 PASS build",
        b'{"nested":{"a":[1,2]}}',
        b"",
        b"not json at all",
    ]
    terminators = [b"\n", b"\r", b"\r\n"]
    payload = b""
    for index, fragment in enumerate(fragments * 3):
        payload += fragment + terminators[index % len(terminators)]

    log = tmp_path / "raw.log"
    log.write_bytes(payload)

    # The oracle: the pre-optimisation implementation, expressed with the
    # standard-library primitive the scan was written to avoid.
    expected: list[tuple[str, int]] = []
    offset = 0
    for line in payload.splitlines(keepends=True):
        stripped = line.strip()
        if stripped:
            try:
                json.loads(stripped)
            except ValueError:
                expected.append(("NON_JSONL", offset))
        offset += len(line)

    observed = _breaks(tmp_path, payload)
    assert observed == expected
    assert observed, "the payload must contain gradeable corruption"


def test_a_trailing_fragment_with_no_terminator_is_still_graded(tmp_path: Path) -> None:
    """A capture cut mid-line is the truncation case; it must not vanish."""
    assert _breaks(tmp_path, b'{"type":"a"}\ntruncated-garbage') == [("NON_JSONL", 13)]


def test_a_line_that_merely_mentions_a_marker_is_still_graded(tmp_path: Path) -> None:
    """The allowlist recognises canonical lines, not lines containing one.

    Extraction is deliberately permissive -- it must find a session id
    inside a decorated TUI line, so it tests the completion marker as a
    SUBSTRING and searches for the id UNANCHORED. Reusing it to decide
    "is this expected content" excused any line that mentioned the
    marker text, and agents routinely echo a ``declare_complete`` result
    into their own prose. A frame severed mid-write then graded CLEAN
    whenever its surviving bytes happened to carry that sentence, which
    is exactly the corruption this detector exists to catch.
    """
    smuggled = {
        "garbage before the marker": b"HELLO GARBAGE Task declared complete: session_id=x",
        "a frame severed mid-write": (
            b'{"type":"item","text":"Task declared complete: session_id=abc, summary='
        ),
        "binary junk then a marker": b"\x01\x02junk Task declared complete: sessionId=1",
        # The mirror image: anchoring only the FRONT excused a line that
        # opened with the marker however it ended, so a whole frame
        # concatenated onto one -- the classic lost-newline interleave --
        # was graded as expected content.
        "a frame concatenated after": (
            b'Task declared complete: session_id=1{"type":"assistant","text":"lost frame"}'
        ),
        # The SAME interleave with no whitespace in the lost frame, on a
        # head that carries a real terminal timestamp. A ``\S+`` tail
        # value swallowed the entire frame here, so the verdict turned
        # on whether the lost frame happened to contain a space -- and
        # real wire frames are compact. Every recorded fixture in this
        # repo graded CLEAN this way.
        # TWO canonical lines run together. ``rfind`` walked to the LAST
        # timestamp, leaving everything before it unconstrained, so a
        # completion line followed by a coordination line -- both
        # emitted by mcp/tools/coordination.py, both ending in a
        # timestamp -- graded CLEAN. One line carries one timestamp
        # field; two means two lines.
        "a coordination line run together with it": (
            b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999"
            b"Coordination action 'checkpoint' processed: session_id=zzz999, timestamp=1700000000"
        ),
        "two completion lines run together": (
            b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999"
            b"Task declared complete: session_id=def456, summary='and again', timestamp=1700000000"
        ),
        # Trailing junk riding on the timestamp VALUE. A looser class
        # than digits (``[0-9A-Za-z._:+-]+``) matched
        # ``timestamp=1699999999abc`` right to the end of the line, so
        # whatever followed the value came along for free -- the emitter
        # interpolates ``now_fn() -> int``, so digits are the whole
        # vocabulary.
        "junk riding on the timestamp value": (
            b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999"
            b"trailingjunk"
        ),
        # The timestamp field INSIDE the head, with nothing after it.
        # Dropping the "tail must come after the head" ordering check
        # let this match: the id itself contains the field name.
        "a timestamp field inside the session id": (
            b"Task declared complete: session_id=timestamp=1"
        ),
        "a compact frame after a complete line": (
            b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999"
            b'{"type":"item.completed","id":"x"}'
        ),
        "the marker then junk": b"Task declared complete: session_id=1 " + b"x" * 200,
    }

    for label, line in smuggled.items():
        assert _breaks(tmp_path, line + b"\n") == [("NON_JSONL", 0)], label


def test_a_summary_may_talk_about_timestamps(tmp_path: Path) -> None:
    """The summary is free text and cannot be second-guessed.

    It is interpolated raw, unbounded and unsanitised, so an agent may
    write anything in it -- including the word ``timestamp`` in field
    shape. Anchoring the tail on the FIRST timestamp field put that
    match inside the summary, failed the digits-to-end test there, and
    reported a byte-perfect emitted line as raw transcript corrupted.
    """
    genuine = (
        b"Task declared complete: session_id=abc123, summary='fixed timestamp: bug', "
        b"timestamp=1699999999",
        b"Task declared complete: session_id=abc123, summary='changed timestamp=0 to "
        b"timestamp=now', timestamp=1699999999",
        b"Task declared complete: session_id=abc123, summary='it's fine', timestamp=17",
    )

    for line in genuine:
        assert _breaks(tmp_path, line + b"\n") == [], line


def test_a_severed_write_glued_to_the_next_line_is_graded(tmp_path: Path) -> None:
    """The characteristic corruption: a torn write plus the next writer.

    Only the INTACT completion-plus-coordination pair was pinned, and
    the strictly more corrupt severed variant -- which is what a torn
    write actually produces -- was excused, because the only surviving
    timestamp belonged to the following line and closed it neatly.

    A summary cannot be validated, but it cannot legitimately contain
    the opening of another canonical message or the start of a JSON wire
    frame either. Those are what a second line leaves behind.
    """
    head = b"Task declared complete: session_id=abc123"
    coordination = (
        b"Coordination action 'checkpoint' processed: session_id=zzz999, timestamp=1700000000"
    )
    progress = b"Progress reported: status='x', note='y', timestamp=1700000001"

    severed = {
        "mid-summary, then a coordination line": head + b", summary='did" + coordination,
        "after the id, then a coordination line": head + b", " + coordination,
        "head only, then a coordination line": head + coordination,
        "mid-summary, then a progress line": head + b", summary='oo" + progress,
        # No closing delimiter: the surviving text merely ENDS in a
        # timestamp field. The tail must identify the summary's closing
        # boundary (``', timestamp=``), not any trailing mention -- a
        # tail that matched a bare ``timestamp=<int>`` anywhere at the
        # end let a severed line close itself on the next writer's
        # fragment.
        "a bare timestamp fragment appended": (
            head + b", summary='did" + b" partial-frame timestamp=99"
        ),
        "a wire frame inside the summary quotes": (
            head + b", summary='x{\"type\":\"item.completed\"}', timestamp=17"
        ),
    }

    for label, line in severed.items():
        assert _breaks(tmp_path, line + b"\n") == [("NON_JSONL", 0)], label


def test_a_genuine_canonical_line_is_still_expected_content(tmp_path: Path) -> None:
    """Not vacuous: the lines the PTY layer really emits stay clean."""
    # The completion line as ``mcp/tools/coordination.py`` actually
    # emits it: marker, id, free-text summary, terminal timestamp.
    canonical = (
        b"Session ID: 0f0f-abcd",
        b"Claude session ready. Session ID: 0f0f-abcd",
        b"Resume this session with --resume 0f0f-abcd",
        b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999",
    )

    for line in canonical:
        assert _breaks(tmp_path, line + b"\n") == [], line


def test_a_canonical_line_grades_the_same_however_long_it_is(tmp_path: Path) -> None:
    """The verdict must not depend on length, for canonical lines either.

    The cost guard skips the normalise pass for a long line, and its
    justification was that no line past the cap could be an allowlisted
    marker. That was false -- the completion branch matched a substring
    with an unanchored id search -- so the same canonical text graded
    clean when short and corrupt when long.
    """
    for pad in (40, 71_680):
        line = (
            b"Task declared complete: session_id=abc123, summary='"
            + b"x" * pad
            + b"', timestamp=1699999999"
        )

        assert _breaks(tmp_path, line + b"\n") == [], pad


def test_an_oversized_number_does_not_take_the_grader_down(tmp_path: Path) -> None:
    """A bare ``ValueError`` must not escape a grader documented as safe.

    CPython caps integer literals at 4300 digits and raises ``ValueError``
    -- not ``JSONDecodeError`` -- past it. That propagated out of two
    callers whose docstrings promise they never raise, taking the
    corruption verdict AND the phase's own verdict line with it.
    """
    oversized = {
        "a bare long integer": b"9" * 5000,
        "a frame carrying one": b'{"type":"item","n":' + b"9" * 5000 + b"}",
        "one nested in an array": b'{"a":[' + b"1" * 4301 + b"]}",
    }

    for label, line in oversized.items():
        assert _breaks(tmp_path, line + b"\n") == [("NON_JSONL", 0)], label

    # Just under the limit is ordinary, well-formed JSONL.
    assert _breaks(tmp_path, b'{"n":' + b"9" * 4300 + b"}\n") == []


def test_a_long_json_scalar_is_reported_for_what_it_is(tmp_path: Path) -> None:
    """The detail text must not accuse a valid JSON value of being unparseable.

    A line that parses but is not an OBJECT is a break -- the capture is
    JSONL -- but calling a 70 KB JSON string "not parseable JSON" sends
    an operator looking for damage that is not there. The cost-skip path
    reported every long line that way, whether it had parsed or not.
    """
    log = tmp_path / "scalar.log"
    log.write_bytes(b'"' + b"x" * 70_000 + b'"\n')

    breaks = detect_raw_log_breaks(log, transport=AgentTransport.CODEX)

    assert [b.kind for b in breaks] == ["NON_JSONL"]
    assert "parses as JSON but is not a JSON object" in breaks[0].detail
    assert "type=str" in breaks[0].detail


def test_deep_nesting_does_not_take_the_grader_down(tmp_path: Path) -> None:
    """``RecursionError`` is not a ``ValueError``, and must not escape either.

    ``json.loads`` exhausts its stack on a long run of ``[`` and raises
    ``RecursionError``, which the widened ``ValueError`` clause does not
    catch. A capture full of ``[`` is exactly what a truncated or
    interleaved write looks like, so the input that breaks the parser is
    the input the grader exists to judge -- and both callers document
    that it never raises, then swallow at DEBUG, so the phase's whole
    verdict line vanished silently.
    """
    log = tmp_path / "deep.log"
    log.write_bytes(b'{"ok":1}\n' + b"[" * 200_000 + b"\n")

    breaks = detect_raw_log_breaks(log, transport=AgentTransport.CODEX)

    assert [b.kind for b in breaks] == ["NON_JSONL"]
    assert breaks[0].offset == 9
