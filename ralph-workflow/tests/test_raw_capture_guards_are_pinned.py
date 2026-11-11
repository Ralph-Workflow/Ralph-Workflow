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
    raw_log_path_for,
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
    def split(self, sep: bytes | None = None, _maxsplit: int = -1) -> list[bytes]:
        msg = "nul_separated_chunks must not materialise the whole payload"
        raise AssertionError(msg)


class _SliceCountingBytes(bytes):
    slice_reads: int
    index_reads: int
    searches: int

    def __new__(cls, payload: bytes) -> _SliceCountingBytes:
        self = super().__new__(cls, payload)
        self.slice_reads = 0
        self.index_reads = 0
        self.searches = 0
        return self

    def find(
        self,
        sub: ReadableBuffer | SupportsIndex,
        start: SupportsIndex | None = None,
        end: SupportsIndex | None = None,
        /,
    ) -> int:
        self.searches += 1
        return bytes(self).find(sub, start, end)

    def __getitem__(self, key: SupportsIndex | slice, /) -> int | bytes:
        if isinstance(key, slice):
            self.slice_reads += 1
        else:
            self.index_reads += 1
        return bytes(self)[key]


def test_the_nul_scan_never_splits_the_whole_payload() -> None:
    payload = _NoSplitBytes(b'{"ok":1}\n' + b"\x00" * 100_000 + b'{"after":1}\n')

    chunks = list(nul_separated_chunks(payload))

    assert [chunk for _, chunk in chunks] == [b'{"ok":1}\n', b'{"after":1}\n']


def test_the_nul_scan_stops_early_when_the_caller_does() -> None:
    payload = _NoSplitBytes(b"a\x00" * 500_000)

    first = next(iter(nul_separated_chunks(payload)))

    assert first == (0, b"a")


def test_an_eviction_write_from_the_reader_is_not_liveness() -> None:
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
        assert "a line the consumer never saw" in written
    finally:
        os.close(master_fd)


def test_a_failing_capture_does_not_cost_the_incoming_line() -> None:
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
    reset_raw_overflow_path_state()
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    direct = RawOverflowLog(real_root, "unit")
    direct.append("first writer")
    direct.close()

    through_link = RawOverflowLog(linked_root, "unit")
    through_link.append("second writer")
    through_link.close()

    written = (real_root / ".agent" / "raw" / "unit.log").read_text(encoding="utf-8")

    assert written.splitlines() == ["first writer", "second writer"]


def test_the_smoke_ceiling_groups_every_headless_stream_transport() -> None:
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
    assert ceiling("claude", None) == _MAX_VISIBLE_OUTPUT_LINES
    assert ceiling("codex exec", "--json") == _MAX_VISIBLE_OUTPUT_LINES


def test_a_decode_error_is_reported_as_truncation_not_teardown() -> None:
    from loguru import logger

    class _UndecodableStdout:
        def __iter__(self) -> _UndecodableStdout:
            return self

        def __next__(self) -> str:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    reset_raw_overflow_path_state()
    handle = _FakeManagedProcess()
    handle.stdout = _UndecodableStdout()
    reader = ProcessLineReader(handle, _make_subprocess_ctx(workspace_path=Path.cwd()), FakeClock())

    records: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda message: records.append((message.record["level"].name, message.record["message"])),
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
    from ralph.display.raw_log_breaks import iter_capture_lines

    line_count = 500
    payload = _FindCountingBytes(b'{"ok":1}\n' * line_count)

    lines = list(iter_capture_lines(payload))

    assert len(lines) == line_count
    assert payload.counts.get(b"\r", 0) == 1, payload.counts


def test_a_verdict_does_not_depend_on_how_long_the_line_is(tmp_path: Path) -> None:
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

    escaped = tmp_path / "escaped.log"
    escaped.write_bytes(
        b'{"type": "item.completed", "text": "\x1b[32mok\x1b[0m ' + b"y" * 200_000 + b'"}\n'
    )
    assert detect_raw_log_breaks(escaped, transport=AgentTransport.CODEX) == []
    assert calls, "a line carrying escapes must still be normalised"


def test_two_roots_for_one_file_get_one_writer(tmp_path: Path) -> None:
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_overflow import detect_raw_log_breaks

    reset_raw_overflow_path_state()
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    direct = get_or_create_raw_overflow_log(real_root, "shared-unit")
    through_link = get_or_create_raw_overflow_log(linked_root, "shared-unit")

    assert direct is through_link, "two roots for one file must share the writer"

    frames = [f'{{"type":"item","seq":{index}}}' for index in range(60)]
    for index, frame in enumerate(frames):
        (direct if index % 2 == 0 else through_link).append(frame)
    direct.close()

    written = (real_root / ".agent" / "raw" / "shared-unit.log").read_text(encoding="utf-8")

    assert written.splitlines() == frames
    assert (
        detect_raw_log_breaks(
            real_root / ".agent" / "raw" / "shared-unit.log", transport=AgentTransport.CODEX
        )
        == []
    )


def test_a_nul_hole_is_skipped_in_windows_not_byte_by_byte() -> None:
    from ralph.display.raw_log_breaks import nul_separated_chunks

    hole = 1 << 18
    payload = _SliceCountingBytes(b'{"ok":1}\n' + b"\x00" * hole + b'{"after":1}\n')

    chunks = list(nul_separated_chunks(payload))

    assert [chunk for _, chunk in chunks] == [b'{"ok":1}\n', b'{"after":1}\n']
    assert payload.index_reads == 0, "the walk must not read single bytes"
    assert payload.slice_reads <= 32, payload.slice_reads
    assert payload.searches <= 8, payload.searches


def test_the_normaliser_skips_its_walk_when_there_is_nothing_to_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.display.vt_normalizer as vt

    consulted: list[int] = []
    real = vt._REWRITE_CONTROLS

    class _CountingPredicate:
        def search(self, text: str) -> object:
            consulted.append(len(text))
            return real.search(text)

    monkeypatch.setattr(vt, "_REWRITE_CONTROLS", _CountingPredicate())

    assert vt.normalize_vt_text("\x1b[32m" + "z" * 4096 + "\x1b[0m") == "z" * 4096

    assert consulted == [4096], "the early-return guard was not consulted"

    monkeypatch.setattr(vt, "_REWRITE_CONTROLS", real)
    assert vt.normalize_vt_text("abc\rdef") == "def"
    assert vt.normalize_vt_text("ab\bc") == "ac"
    assert vt.normalize_vt_text("a\nb") == "a\nb"

    class _NeverRewrites:
        def search(self, text: str) -> object:
            return None

    monkeypatch.setattr(vt, "_REWRITE_CONTROLS", _NeverRewrites())

    assert vt.normalize_vt_text("abc\rdef") == "abc\rdef", (
        "the guard was consulted but not returned on"
    )


def test_the_stale_writer_cannot_truncate_the_next_run(tmp_path: Path) -> None:
    reset_raw_overflow_path_state()
    first = get_or_create_raw_overflow_log(tmp_path, "shared")
    first.append("B" * 10)

    reset_raw_overflow_path_state()
    second = get_or_create_raw_overflow_log(tmp_path, "shared")
    second.append("written after the reset")
    first.append("a late write from the forgotten writer")
    second.close()

    written = raw_log_path_for(tmp_path, "shared").read_bytes()

    assert b"\x00" not in written, "a stale writer punched a hole in the new run's capture"
    assert written == b"written after the reset\n", written
    assert first.is_disabled
