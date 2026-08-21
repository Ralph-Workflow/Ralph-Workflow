"""Guards in the raw-capture path that no test previously held down.

Each of these was reverted one at a time by an independent mutation
sweep and the whole suite stayed green — including several fixes whose
own commit messages claimed they were pinned. A guard nothing asserts is
a guard the next refactor removes for free, and every one of these
protects a property the capture is graded on.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._bounded_lines_queue import BoundedLinesQueue
from ralph.agents.invoke._process_reader import ProcessLineReader
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.agent_config import AgentConfig
from ralph.display.raw_overflow import (
    RawOverflowLog,
    get_or_create_raw_overflow_log,
    nul_separated_chunks,
    reset_raw_overflow_path_state,
)
from tests.agents.invoke.test_line_reader_queue_bound import (
    _FakeManagedProcess,
    _FakePtyHandle,
    _make_pty_ctx,
    _make_subprocess_ctx,
)

if TYPE_CHECKING:
    from typing import SupportsIndex

    import pytest
    from _typeshed import ReadableBuffer


class _NoSplitBytes(bytes):
    """``bytes`` that refuses to be split whole.

    ``nul_separated_chunks`` must scan lazily: ``payload.split(b"\\x00")``
    allocates one object per NUL BEFORE any grading runs, which is what
    made a NUL hole cost 7.9 s and 517 MB at the file cap, none of it
    bounded by the break cap. An eager implementation returns byte-for-
    byte identical output, so no assertion on the RESULT can catch it —
    and ``inspect.isgenerator`` passes for a generator that splits
    internally. This makes the forbidden call itself fail.
    """

    def split(self, sep: bytes | None = None, maxsplit: int = -1) -> list[bytes]:
        msg = "nul_separated_chunks must not materialise the whole payload"
        raise AssertionError(msg)


def test_the_nul_scan_never_splits_the_whole_payload() -> None:
    """Laziness pinned by forbidding the call, not by timing it."""
    payload = _NoSplitBytes(b'{"ok":1}\n' + b"\x00" * 100_000 + b'{"after":1}\n')

    chunks = list(nul_separated_chunks(payload))

    assert [chunk for _, chunk in chunks] == [b'{"ok":1}\n', b'{"after":1}\n']


def test_the_nul_scan_stops_early_when_the_caller_does() -> None:
    """A caller that stops at the break cap must not pay for the rest."""
    payload = _NoSplitBytes(b"a\x00" * 500_000)

    first = next(iter(nul_separated_chunks(payload)))

    assert first == (0, b"a")


def test_an_eviction_write_from_the_reader_is_not_liveness() -> None:
    """Pinned at the READER's call site, not just on the log's flag.

    The idle watchdog reads ``size_bytes`` to decide the unit is still
    progressing. Eviction writes come from the producer and mean the
    CONSUMER fell behind, so counting them lets a wedged consumer look
    alive for as long as the agent keeps talking. The existing test set
    the flag directly on ``RawOverflowLog``; the reader could still pass
    the default and nothing failed.
    """
    reset_raw_overflow_path_state()
    reader = ProcessLineReader(
        _FakeManagedProcess(), _make_subprocess_ctx(workspace_path=Path.cwd()), FakeClock()
    )
    log = reader._raw_overflow
    log.append("a line the consumer handled")
    before = log.size_bytes

    reader._capture_evicted_line("a line the consumer never saw")

    assert log.size_bytes == before
    log.close()


def test_a_pty_eviction_write_is_not_liveness_either(tmp_path: Path) -> None:
    """The PTY reader has its own call site, and it was unpinned.

    The process reader's was covered; the identical
    ``_pty_line_reader`` call site could pass the default and nothing
    failed. Both feed the same idle watchdog, which reads ``size_bytes``
    to decide the unit is still progressing -- and an eviction means the
    consumer fell BEHIND, so counting it lets a wedged consumer look
    alive for as long as the agent keeps talking.
    """
    reset_raw_overflow_path_state()
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        reader = PtyLineReader(
            _FakePtyHandle(master_fd),
            "test-agent",
            _make_pty_ctx(workspace_path=tmp_path),
            FakeClock(),
            extras=None,
        )
        reader._raw_overflow = get_or_create_raw_overflow_log(tmp_path, "pty-liveness-unit")
        log = reader._raw_overflow
        log.append("a line the consumer handled")
        before = log.size_bytes

        reader._capture_evicted_line("a line the consumer never saw")

        assert log.size_bytes == before
        log.close()
        written = (tmp_path / ".agent" / "raw" / "pty-liveness-unit.log").read_text(
            encoding="utf-8"
        )
        # Withheld from the liveness claim, NOT from the transcript.
        assert "a line the consumer never saw" in written
    finally:
        os.close(master_fd)


def test_a_failing_capture_does_not_cost_the_incoming_line() -> None:
    """A sink that raises must not propagate out of append/extend.

    The exception escaped the queue and dropped the INCOMING line before
    it was ever queued — turning a capture problem into data loss on the
    live path, which is the opposite of the sink's purpose.
    """
    queue = BoundedLinesQueue(maxlen=2)

    def explode(_line: str) -> None:
        raise OSError("the capture file went away")

    queue.set_eviction_sink(explode)
    queue.extend(["a", "b"])
    queue.append("c")

    assert queue.snapshot() == ["b", "c"]


def test_the_pty_capture_is_open_before_any_thread_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering, asserted as ordering.

    The read thread fills the bounded queue, and the queue's eviction
    sink discards the line while ``_raw_overflow`` is still None — so a
    burst arriving between the thread start and the capture open was
    lost in exactly the way the sink exists to prevent.
    """
    reset_raw_overflow_path_state()
    master_fd = os.open("/dev/null", os.O_RDONLY)
    observed: list[bool] = []
    try:
        reader = PtyLineReader(
            _FakePtyHandle(master_fd),
            "test-agent",
            _make_pty_ctx(workspace_path=tmp_path),
            FakeClock(),
            extras=None,
        )
        original = reader._start_thread

        def recording_start(target: object) -> object:
            observed.append(reader._raw_overflow is not None)
            return original(target)

        monkeypatch.setattr(reader, "_start_thread", recording_start)
        setup = reader._setup_read_loop()
        try:
            assert observed, "no thread was started"
            assert all(observed), "a thread started before the capture was open"
        finally:
            reader._teardown_read_loop(setup, interrupted=False)
    finally:
        os.close(master_fd)


def test_two_writers_for_one_resolved_path_share_their_state(tmp_path: Path) -> None:
    """The path key resolves symlinks, so a second writer cannot truncate.

    The first write of a run truncates and later writers append. Keying
    on the spelling instead of the resolved path let the same file be
    reached by a symlinked or relative root under two keys, re-arming
    the truncation that state exists to prevent.
    """
    reset_raw_overflow_path_state()
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    # Constructed directly, NOT through the registry: the registry's own
    # key already resolves, so it hands back one instance and hides
    # whether ``_PATH_STATE`` agrees. Two independently-constructed
    # writers for one file are exactly the case that state exists for.
    direct = RawOverflowLog(real_root, "unit")
    direct.append("first writer")
    direct.close()

    through_link = RawOverflowLog(linked_root, "unit")
    through_link.append("second writer")
    through_link.close()

    written = (real_root / ".agent" / "raw" / "unit.log").read_text(encoding="utf-8")

    assert written.splitlines() == ["first writer", "second writer"]


def test_the_smoke_ceiling_groups_every_headless_stream_transport() -> None:
    """The ceiling classifier must not be the capture identity.

    They answer opposite questions: the identity keeps agents APART so
    each owns a capture, the ceiling groups them TOGETHER because they
    share an output shape. Reusing one for both silently re-tuned this
    ceiling from 250 to 80 for every ccs and kimi run.
    """
    from ralph.pipeline.plumbing.smoke_plumbing import (
        _MAX_VISIBLE_OUTPUT_LINES,
        _MAX_VISIBLE_OUTPUT_LINES_BY_AGENT,
        _resolve_visible_output_agent_prefix,
    )

    def ceiling(cmd: str, output_flag: str | None) -> int:
        config = AgentConfig(cmd=cmd, output_flag=output_flag)
        prefix = _resolve_visible_output_agent_prefix(config)
        return _MAX_VISIBLE_OUTPUT_LINES_BY_AGENT.get(prefix, _MAX_VISIBLE_OUTPUT_LINES)

    stream_json = "--output-format=stream-json"

    assert ceiling("claude -p", stream_json) == 250
    assert ceiling("ccs glm", stream_json) == 250
    assert ceiling("kimi", stream_json) == 250
    # Not vacuous: a transport that does not speak that wire keeps the default.
    assert ceiling("claude", None) == _MAX_VISIBLE_OUTPUT_LINES
    assert ceiling("codex exec", "--json") == _MAX_VISIBLE_OUTPUT_LINES


def test_a_decode_error_is_reported_as_truncation_not_teardown() -> None:
    """``UnicodeDecodeError`` is a ``ValueError`` subclass.

    The stream-close clause below it therefore swallowed a genuine decode
    truncation at DEBUG and told the operator the reader "stopped at
    stream close" — the opposite of the truth. The transcript is cut
    mid-run in that case, which is the exact failure this reader is
    instrumented to report.
    """
    from loguru import logger

    class _UndecodableStdout:
        def __iter__(self) -> _UndecodableStdout:
            return self

        def __next__(self) -> str:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    reset_raw_overflow_path_state()
    handle = _FakeManagedProcess()
    handle.stdout = _UndecodableStdout()
    reader = ProcessLineReader(
        handle, _make_subprocess_ctx(workspace_path=Path.cwd()), FakeClock()
    )

    records: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda message: records.append(
            (message.record["level"].name, message.record["message"])
        ),
        level="DEBUG",
    )
    try:
        reader._read_thread()
    finally:
        logger.remove(sink_id)
        reader._raw_overflow.close()

    messages = " ".join(text for _level, text in records)
    warned = [level for level, text in records if "undecodable byte" in text]

    assert "undecodable byte" in messages, messages
    assert "stopped at stream close" not in messages, messages
    assert warned == ["WARNING"], records


class _FindCountingBytes(bytes):
    """``bytes`` that counts how often each terminator is searched for.

    The line scan must carry both terminator positions and re-search only
    after passing one. Searching from the current offset on EVERY line
    made it O(lines x bytes) -- ``find`` scans to EOF when the byte is
    absent, and a healthy JSONL capture contains no ``\\r`` at all -- so
    grading a healthy 16 MB capture took 38 seconds, on every phase
    close, for every verdict.

    The cost cannot be asserted with a clock (the repo forbids wall-clock
    assertions, and rightly: they flake under load). The CALL COUNT is
    the structural equivalent and is exact.
    """

    counts: dict[bytes, int]

    def __new__(cls, payload: bytes) -> _FindCountingBytes:
        self = super().__new__(cls, payload)
        self.counts = {}
        return self

    def find(
        self,
        sub: ReadableBuffer | SupportsIndex,
        start: SupportsIndex | None = None,
        end: SupportsIndex | None = None,
        /,
    ) -> int:
        if isinstance(sub, bytes):
            self.counts[sub] = self.counts.get(sub, 0) + 1
        return bytes(self).find(sub, start, end)


def test_the_line_scan_does_not_research_an_absent_terminator() -> None:
    """A carriage return that is not there is looked for once, not per line."""
    from ralph.display.raw_log_breaks import iter_capture_lines

    line_count = 500
    payload = _FindCountingBytes(b'{"ok":1}\n' * line_count)

    lines = list(iter_capture_lines(payload))

    assert len(lines) == line_count
    # One search for the absent \r, not one per line.
    assert payload.counts.get(b"\r", 0) == 1, payload.counts


def test_a_verdict_does_not_depend_on_how_long_the_line_is(tmp_path: Path) -> None:
    """The same frame must grade the same at any length.

    A frame carrying RAW VT escapes fails a strict parse of its bytes and
    is rescued by the decode/normalise pass. Truncating that pass's input
    to bound its cost made the rescue length-dependent: the identical
    codex frame graded clean at 160 bytes and "raw transcript corrupted"
    at 80 KB -- inventing the exact false verdict this detector exists to
    avoid, and quoting valid JSON as the proof.
    """
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_overflow import detect_raw_log_breaks

    verdicts = []
    for size in (100, 80_000, 200_000):
        frame = (
            b'{"type": "item.completed", "text": "tool said '
            b"\x1b[32mok\x1b[0m " + b"y" * size + b'"}\n'
        )
        log = tmp_path / f"frame-{size}.log"
        log.write_bytes(frame)
        verdicts.append(
            [(b.kind, b.offset) for b in detect_raw_log_breaks(log, transport=AgentTransport.CODEX)]
        )

    assert verdicts == [[], [], []], verdicts


def test_a_long_line_hiding_garbage_past_the_cap_is_still_caught(tmp_path: Path) -> None:
    """The other direction: truncation must not manufacture a false CLEAN.

    A complete JSON object followed by garbage on the SAME line does not
    parse. Grading only the head made the head parse, and the line was
    skipped.
    """
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_overflow import detect_raw_log_breaks

    for pad in (65_526, 65_536, 70_000):
        head = b'{"a":"' + b"z" * pad + b'"}'
        log = tmp_path / f"tail-{pad}.log"
        log.write_bytes(head + b"THIS-IS-GARBAGE-NOT-JSON\n")

        breaks = detect_raw_log_breaks(log, transport=AgentTransport.CODEX)

        assert [b.kind for b in breaks] == ["NON_JSONL"], (pad, breaks)


def test_a_long_plain_line_is_graded_without_the_normalise_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cost is bounded by SKIPPING the pass, not by shortening its input.

    Shortening the input made the verdict length-dependent. Skipping it
    is safe only where it provably cannot change the answer -- a line of
    pure printable ASCII has nothing for the normaliser to remove -- so
    the guard is asserted where it applies AND where it must not.

    Structural, because the cost this protects cannot be asserted with a
    clock: the pass allocates several full-size copies of a line that can
    be tens of megabytes.
    """
    import ralph.display.raw_log_breaks as breaks_module
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_overflow import detect_raw_log_breaks

    calls: list[int] = []
    real = breaks_module.normalize_vt_text

    def counting(text: str) -> str:
        calls.append(len(text))
        return real(text)

    monkeypatch.setattr(breaks_module, "normalize_vt_text", counting)

    plain = tmp_path / "plain.log"
    plain.write_bytes(b"z" * 200_000 + b"\n")
    assert [b.kind for b in detect_raw_log_breaks(plain, transport=AgentTransport.CODEX)] == [
        "NON_JSONL"
    ]
    assert calls == [], "a long printable line must not reach the normalise pass"

    # And the guard must NOT swallow a line the pass would rescue: an
    # escape byte makes it non-printable, so the pass still runs.
    escaped = tmp_path / "escaped.log"
    escaped.write_bytes(
        b'{"type": "item.completed", "text": "\x1b[32mok\x1b[0m ' + b"y" * 200_000 + b'"}\n'
    )
    assert detect_raw_log_breaks(escaped, transport=AgentTransport.CODEX) == []
    assert calls, "a line carrying escapes must still be normalised"
