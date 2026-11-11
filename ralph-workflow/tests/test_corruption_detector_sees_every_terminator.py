from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.agents.invoke import starts_with_canonical_session_marker
from ralph.config.enums import AgentTransport
from ralph.display.raw_log_breaks import detect_raw_log_breaks, iter_capture_lines

_FIELD_PAYLOAD = b'{"type":"item.started"}\n--- \r{"type":"item.completed"}\n'


def _breaks(tmp_path: Path, payload: bytes) -> list[tuple[str, int]]:
    log = tmp_path / "raw.log"
    log.write_bytes(payload)
    return [
        (break_.kind, break_.offset)
        for break_ in detect_raw_log_breaks(log, transport=AgentTransport.CODEX)
    ]


def test_a_bare_carriage_return_still_separates_a_corrupt_line(tmp_path: Path) -> None:
    assert _breaks(tmp_path, _FIELD_PAYLOAD) == [("NON_JSONL", 24)]


def test_crlf_is_one_terminator_and_not_a_break(tmp_path: Path) -> None:
    payload = b'{"type":"a"}\r\n{"type":"b"}\r\n{"type":"c"}\r\n'

    assert _breaks(tmp_path, payload) == []
    assert list(iter_capture_lines(payload)) == payload.splitlines(keepends=True)
    assert len(list(iter_capture_lines(payload))) == 3


def test_the_scan_agrees_with_the_splitlines_oracle_it_replaced(tmp_path: Path) -> None:
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
    assert _breaks(tmp_path, b'{"type":"a"}\ntruncated-garbage') == [("NON_JSONL", 13)]


def test_a_line_that_merely_mentions_a_marker_is_still_graded(tmp_path: Path) -> None:
    smuggled = {
        "garbage before the marker": b"HELLO GARBAGE Task declared complete: session_id=x",
        "a frame severed mid-write": (
            b'{"type":"item","text":"Task declared complete: session_id=abc, summary='
        ),
        "binary junk then a marker": b"\x01\x02junk Task declared complete: sessionId=1",
        "a frame concatenated after": (
            b'Task declared complete: session_id=1{"type":"assistant","text":"lost frame"}'
        ),
        "a coordination line run together with it": (
            b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999"
            b"Coordination action 'checkpoint' processed: session_id=zzz999, timestamp=1700000000"
        ),
        "two completion lines run together": (
            b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999"
            b"Task declared complete: session_id=def456, summary='and again', timestamp=1700000000"
        ),
        "junk riding on the timestamp value": (
            b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999"
            b"trailingjunk"
        ),
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
    genuine = (
        b"Task declared complete: session_id=abc123, summary='fixed timestamp: bug', "
        b"timestamp=1699999999",
        b"Task declared complete: session_id=abc123, summary='changed timestamp=0 to "
        b"timestamp=now', timestamp=1699999999",
        b'Task declared complete: session_id=abc123, summary="it"s fine", timestamp=17'.replace(
            b'summary="', b"summary='"
        ).replace(b'fine"', b"fine'"),
    )

    for line in genuine:
        assert _breaks(tmp_path, line + b"\n") == [], line


def test_a_severed_write_glued_to_the_next_line_is_graded(tmp_path: Path) -> None:
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
        "a bare timestamp fragment appended": (
            head + b", summary='did" + b" partial-frame timestamp=99"
        ),
    }

    for label, line in severed.items():
        assert _breaks(tmp_path, line + b"\n") == [("NON_JSONL", 0)], label

    inside_the_quotes = head + b', summary=\'x{"type":"item.completed"}\', timestamp=17'
    assert _breaks(tmp_path, inside_the_quotes + b"\n") == []


def test_a_genuine_canonical_line_is_still_expected_content(tmp_path: Path) -> None:
    canonical = (
        b"Session ID: 0f0f-abcd",
        b"Claude session ready. Session ID: 0f0f-abcd",
        b"Resume this session with --resume 0f0f-abcd",
        b"Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999",
    )

    for line in canonical:
        assert _breaks(tmp_path, line + b"\n") == [], line


@pytest.mark.timeout_seconds(5)
def test_a_canonical_line_grades_the_same_however_long_it_is(tmp_path: Path) -> None:
    for pad in (40, 71_680):
        line = (
            b"Task declared complete: session_id=abc123, summary='"
            + b"x" * pad
            + b"', timestamp=1699999999"
        )

        assert _breaks(tmp_path, line + b"\n") == [], pad


def test_an_oversized_number_does_not_take_the_grader_down(tmp_path: Path) -> None:
    oversized = {
        "a bare long integer": b"9" * 5000,
        "a frame carrying one": b'{"type":"item","n":' + b"9" * 5000 + b"}",
        "one nested in an array": b'{"a":[' + b"1" * 4301 + b"]}",
    }

    for label, line in oversized.items():
        assert _breaks(tmp_path, line + b"\n") == [("NON_JSONL", 0)], label

    assert _breaks(tmp_path, b'{"n":' + b"9" * 4300 + b"}\n") == []


def test_a_long_json_scalar_is_reported_for_what_it_is(tmp_path: Path) -> None:
    log = tmp_path / "scalar.log"
    log.write_bytes(b'"' + b"x" * 70_000 + b'"\n')

    breaks = detect_raw_log_breaks(log, transport=AgentTransport.CODEX)

    assert [b.kind for b in breaks] == ["NON_JSONL"]
    assert "parses as JSON but is not a JSON object" in breaks[0].detail
    assert "type=str" in breaks[0].detail


_ = starts_with_canonical_session_marker


@pytest.mark.timeout_seconds(10)
def test_deep_nesting_does_not_take_the_grader_down(tmp_path: Path) -> None:
    log = tmp_path / "deep.log"
    log.write_bytes(b'{"ok":1}\n' + b"[" * 200_000 + b"\n")

    breaks = detect_raw_log_breaks(log, transport=AgentTransport.CODEX)

    assert [b.kind for b in breaks] == ["NON_JSONL"]
    assert breaks[0].offset == 9
