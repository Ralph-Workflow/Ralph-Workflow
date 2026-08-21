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
from ralph.display.raw_overflow import detect_raw_log_breaks

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

    Counting ``\r`` and ``\n`` separately would emit an empty line
    between every pair of frames and report a clean file as corrupt.
    """
    payload = b'{"type":"a"}\r\n{"type":"b"}\r\n{"type":"c"}\r\n'
    assert _breaks(tmp_path, payload) == []


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
