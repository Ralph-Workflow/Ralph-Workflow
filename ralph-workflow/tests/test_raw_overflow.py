from __future__ import annotations

import asyncio
import io
import sys
import threading
import weakref
from collections.abc import Sequence
from pathlib import Path

import pytest

from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.display.raw_overflow import RawOverflowLog


def test_append_writes_lines(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line one")
    log.append("line two")
    log.flush()
    content = log.path.read_text(encoding="utf-8")
    assert "line one\n" in content
    assert "line two\n" in content
    log.close()


def test_first_write_of_a_run_truncates_previous_content(tmp_path: Path) -> None:
    from ralph.display.raw_overflow import reset_raw_overflow_path_state

    log1 = RawOverflowLog(tmp_path, "unit-1")
    log1.append("run1 line")
    log1.close()

    reset_raw_overflow_path_state()

    log2 = RawOverflowLog(tmp_path, "unit-1")
    log2.append("run2 line")
    log2.flush()

    content = log2.path.read_text(encoding="utf-8")
    assert "run1 line" not in content
    assert "run2 line" in content
    log2.close()


def test_a_later_writer_in_one_run_continues_the_file(tmp_path: Path) -> None:
    log1 = RawOverflowLog(tmp_path, "unit-1")
    log1.append("wave1 line")
    log1.close()

    log2 = RawOverflowLog(tmp_path, "unit-1")
    log2.append("wave2 line")
    log2.flush()

    content = log2.path.read_text(encoding="utf-8")
    assert "wave1 line" in content, "the earlier wave was erased"
    assert "wave2 line" in content
    log2.close()


def test_the_byte_cap_survives_a_new_writer(tmp_path: Path) -> None:
    log1 = RawOverflowLog(tmp_path, "unit-1", max_bytes=40)
    log1.append("x" * 30)
    log1.close()

    log2 = RawOverflowLog(tmp_path, "unit-1", max_bytes=40)

    assert log2.size_bytes == 0
    assert log2.append("y" * 30) is False
    assert log2.is_disabled


def test_unit_id_sanitization(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit/with:special chars!")
    log.append("test")
    log.flush()
    assert log.path.name == "unit_with_special_chars.log"
    assert log.path.exists()
    log.close()


def test_unit_id_with_model_pairs_with_rendered_record(tmp_path: Path) -> None:
    from ralph.display.record_writer import rendered_record_path, safe_id_for

    log = RawOverflowLog(tmp_path, "pi", model="minimax-MiniMax-3")
    log.append("verbatim line")
    log.flush()
    rendered = rendered_record_path(tmp_path, "pi", model="minimax-MiniMax-3")
    shared_id = safe_id_for("pi", "minimax-MiniMax-3")
    assert log.path.name == f"{shared_id}.log"
    assert rendered.name == f"{shared_id}.rendered.log"
    assert log.path.parent == rendered.parent
    log.close()


def test_relative_reference(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    ref = log.relative_reference(tmp_path)
    assert ref == ".agent/raw/unit-1.log"


def test_relative_reference_absolute_fallback(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    other_root = Path("/some/other/path")
    ref = log.relative_reference(other_root)
    assert ref == log.path.as_posix()


def test_silent_noop_when_parent_is_a_file(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    raw_file = agent_dir / "raw"
    raw_file.write_text("not a directory", encoding="utf-8")

    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("test line")
    assert not log.path.is_file()


def test_thread_safety(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    errors: list[Exception] = []

    def write_lines() -> None:
        try:
            for i in range(20):
                log.append(f"line {i}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_lines) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    log.close()


def test_append_strips_trailing_newline(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line with newline\n")
    log.flush()
    content = log.path.read_text(encoding="utf-8")
    assert content == "line with newline\n"
    assert not content.endswith("\n\n")
    log.close()


def test_append_hard_stops_at_max_bytes(tmp_path: Path) -> None:
    max_bytes = 16
    log = RawOverflowLog(tmp_path, "unit-1", max_bytes=max_bytes)

    assert log.append("1234567") is True
    assert log.append("abcdefg") is True
    assert log.append("overflow") is False

    log.flush()
    assert log.path.stat().st_size == max_bytes
    assert log.path.read_text(encoding="utf-8") == "1234567\nabcdefg\n"


def test_size_bytes_returns_zero_before_first_write(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    assert not log.path.exists()
    assert log.size_bytes == 0


def test_size_bytes_uses_fast_path_after_first_write(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line one")
    expected = len(b"line one\n")
    assert log.size_bytes == expected
    log.close()


def test_size_bytes_returns_bytes_written_when_disabled(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("first line")
    log.disable()
    assert log.size_bytes == len(b"first line\n")


def test_size_bytes_authoritative_even_when_file_unlinked_after_write(
    tmp_path: Path,
) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("some content")
    log.flush()
    expected = len(b"some content\n")
    assert log.path.exists()
    log.path.unlink()
    assert not log.path.exists()
    assert log.size_bytes == expected
    log.close()


def test_size_bytes_returns_zero_when_prior_run_file_exists(tmp_path: Path) -> None:
    log1 = RawOverflowLog(tmp_path, "unit-1")
    log1.append("prior run content")
    log1.flush()
    prior_size = log1.path.stat().st_size
    assert prior_size > 0
    log1.close()

    log2 = RawOverflowLog(tmp_path, "unit-1")
    assert log2.size_bytes == 0

    log2.append("current run content")
    assert log2.size_bytes == len(b"current run content\n")
    log2.close()


def test_is_disabled_true_after_max_bytes(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1", max_bytes=16)
    assert log.is_disabled is False

    log.append("1234567")
    assert log.is_disabled is False

    log.append("abcdefg")
    assert log.is_disabled is False

    log.append("overflow attempt")
    assert log.is_disabled is True


def test_is_disabled_true_after_io_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    raw_dir = agent_dir / "raw"
    raw_dir.mkdir()

    raw_file = raw_dir / "unit-1.log"
    raw_file.write_text("content", encoding="utf-8")
    original_open = Path.open

    def fail_target_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == raw_file:
            raise PermissionError("permission denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_target_open)

    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("new content")
    assert log.is_disabled is True


def test_append_keeps_handle_open_and_buffers(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=3600.0)
    log.append("buffered line")
    assert log.size_bytes == len(b"buffered line\n")
    log.flush()
    assert "buffered line\n" in log.path.read_text(encoding="utf-8")
    log.close()


def test_time_based_flush(tmp_path: Path) -> None:
    fake_time = [0.0]
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=5.0, now=lambda: fake_time[0])
    log.append("first")
    fake_time[0] = 6.0
    log.append("second")
    log.close()
    content = log.path.read_text(encoding="utf-8")
    assert "first\n" in content
    assert "second\n" in content


def test_close_flushes_and_reopen_appends(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=3600.0)
    log.append("before close")
    log.close()
    assert "before close\n" in log.path.read_text(encoding="utf-8")
    log.append("after close")
    log.close()
    content = log.path.read_text(encoding="utf-8")
    assert "before close\n" in content
    assert "after close\n" in content


def test_close_is_idempotent(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("x")
    log.close()
    log.close()


@pytest.mark.asyncio
async def test_executor_drop_unit_closes_raw_log(tmp_path: Path) -> None:
    from ralph.display.activity_router import ActivityRouter
    from ralph.display.raw_overflow import get_or_create_raw_overflow_log
    from ralph.pipeline.work_units import WorkUnit
    from ralph.process import ProcessManager, ProcessManagerPolicy
    from ralph.testing.fake_process import FakeControllableAsyncProcess, FakePsutil

    capture = get_or_create_raw_overflow_log(tmp_path, "unit-x", flush_interval_seconds=3600.0)
    capture_path = capture.path
    assert capture_path == tmp_path / ".agent" / "raw" / "unit-x.log"

    completion = asyncio.Event()
    completion.set()

    async def async_factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeControllableAsyncProcess:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return FakeControllableAsyncProcess(
            pid=4242,
            stdout_data=b"pending line\n",
            completion_event=completion,
        )

    pm = ProcessManager(
        async_process_factory=async_factory,
        psutil=FakePsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )
    executor = SubprocessAgentExecutor(
        command=(sys.executable, "-c", "pass"),
        activity_router=ActivityRouter(),
        raw_overflow_root=tmp_path,
        _pm=pm,
    )

    await executor.run(
        WorkUnit(unit_id="unit-x", description="drop-unit capture"),
        on_output=lambda _line: None,
        on_status=lambda _status: None,
    )

    assert capture.size_bytes > 0, "the run must have appended the agent's line to the capture"
    assert capture_path.read_text(encoding="utf-8") == "", (
        "the agent's line must still be buffered, not on disk, before the unit is dropped"
    )

    executor.drop_unit("unit-x")

    assert "pending line\n" in capture_path.read_text(encoding="utf-8")


def test_weakref_finalize_closes_handle_on_gc(tmp_path: Path) -> None:
    import weakref

    log = RawOverflowLog(tmp_path, "u1")
    assert log.append("survive until GC\n") is True
    log.flush()
    path = log.path
    assert log.size_bytes == len(b"survive until GC\n")
    assert path.read_bytes() == b"survive until GC\n"

    weak_log: weakref.ref[RawOverflowLog] = weakref.ref(log)
    del log

    assert weak_log() is None, (
        "the instance must be garbage-collected after dropping the last strong reference"
    )
    on_disk = path.read_bytes()
    assert b"survive until GC\n" in on_disk, (
        "the buffered payload must have hit disk before finalization"
    )
    fresh = RawOverflowLog(tmp_path, "u1")
    try:
        assert fresh is not None
        assert fresh.size_bytes == 0
        assert path.read_bytes() == b"survive until GC\n"
    finally:
        fresh.close()


def test_registry_holds_weak_reference_only(tmp_path: Path) -> None:
    from ralph.display.raw_overflow import get_or_create_raw_overflow_log

    first = get_or_create_raw_overflow_log(tmp_path, "u-weak-registry")
    first.append("register check\n")
    first.flush()

    weak_first: weakref.ref[RawOverflowLog] = weakref.ref(first)
    del first
    assert weak_first() is None, (
        "DA-001 invariant: the previous instance must be reaped "
        "after the caller drops its reference"
    )

    second = get_or_create_raw_overflow_log(tmp_path, "u-weak-registry")
    assert second is not None
    assert second.append("after-reap\n") is True
    second.flush()
    on_disk = second.path.read_bytes()
    assert b"after-reap\n" in on_disk
    second.close()


def test_close_all_raw_overflow_logs_closes_all_handles(tmp_path: Path) -> None:
    from ralph.display.raw_overflow import (
        close_all_raw_overflow_logs,
        get_or_create_raw_overflow_log,
    )

    log_a = get_or_create_raw_overflow_log(tmp_path, "u-a")
    log_b = get_or_create_raw_overflow_log(tmp_path, "u-b")
    assert log_a.append("a\n") is True
    assert log_b.append("b\n") is True

    close_all_raw_overflow_logs()

    a_on_disk = log_a.path.read_bytes()
    b_on_disk = log_b.path.read_bytes()
    assert b"a\n" in a_on_disk
    assert b"b\n" in b_on_disk

    assert log_a.is_disabled is True, (
        "close_all_raw_overflow_logs must release every buffered handle"
    )
    assert log_b.is_disabled is True
    assert log_a.append("after-close-all\n") is False
    assert log_b.append("after-close-all\n") is False

    log_a.close()
    log_b.close()


def test_long_thinking_emits_one_close_line_no_checkpoints_handle_closed(
    tmp_path: Path,
) -> None:
    from rich.console import Console

    from ralph.display.activity_model import ActivityEventKind
    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    unit_id = "u1"
    long_content = (
        "Fragment of long thinking stream with extra text to exceed the "
        "condenser soft limit and force the verbatim overflow log to open."
    )
    for i in range(25):
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content=f"{i:02d} {long_content}",
            metadata={},
        )
    pd.emit_parsed_event(
        unit_id=unit_id,
        kind=ActivityEventKind.TEXT,
        content="Done.",
        metadata={},
    )
    out = buf.getvalue()
    assert "00 " + long_content in out, "joined passage must be in the rendered output"

    overflow = pd._condensed_logs.get(unit_id)
    assert overflow is not None
    assert overflow.size_bytes > 0, (
        "fixture must trigger the raw-overflow append path so the "
        "buffered handle is open at this point"
    )
    overflow.flush()
    raw_path = overflow.path
    on_disk_during_run = raw_path.read_bytes()
    assert len(on_disk_during_run) > 0, "the appended block must hit disk after flush"

    pd.drop_unit(unit_id)

    assert overflow.is_disabled is True
    assert overflow.append("after-drop\n") is False


def test_cap_warning_is_emitted_once_per_file(tmp_path: Path) -> None:
    from loguru import logger

    from ralph.display.raw_overflow import reset_raw_overflow_path_state

    reset_raw_overflow_path_state()
    messages: list[str] = []
    sink_id = logger.add(lambda record: messages.append(str(record)), level="WARNING")
    try:
        for _ in range(3):
            log = RawOverflowLog(tmp_path, "unit-cap", max_bytes=8)
            log.append("x" * 32)
            log.close()
    finally:
        logger.remove(sink_id)
        reset_raw_overflow_path_state()

    capped = [m for m in messages if "reached its" in m]
    assert len(capped) == 1, capped


def test_gc_actually_closes_the_buffered_handle(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-finalize")
    log.append("payload")
    handle_box = log._handle_box
    assert handle_box[0] is not None, "precondition: the handle is open"
    assert not handle_box[0].closed

    del log

    assert handle_box[0] is None, "the finalizer left the handle open"


def test_dropping_a_log_emits_no_resource_warning(tmp_path: Path) -> None:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        log = RawOverflowLog(tmp_path, "unit-warn")
        log.append("payload")
        del log


def test_detection_is_bounded_on_a_badly_corrupted_capture(tmp_path: Path) -> None:
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_overflow import MAX_REPORTED_BREAKS, detect_raw_log_breaks

    raw_path = tmp_path / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"not json\n" * 50_000)

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CODEX)

    assert len(breaks) == MAX_REPORTED_BREAKS
    assert breaks[0].kind == "NON_JSONL"


def test_a_nul_hole_yields_one_chunk_not_one_per_byte(tmp_path: Path) -> None:
    del tmp_path
    from ralph.display.raw_overflow import nul_separated_chunks

    payload = b'{"ok":1}\n' + b"\x00" * 100_000 + b'{"after":1}\n'

    chunks = list(nul_separated_chunks(payload))

    assert len(chunks) == 2, f"a NUL run must not yield a chunk per byte: {len(chunks)}"
    assert chunks[0][1] == b'{"ok":1}\n'
    assert chunks[1][1] == b'{"after":1}\n'
    assert chunks[1][0] == len(b'{"ok":1}\n') + 100_000


def test_a_nul_hole_is_still_reported_as_corruption(tmp_path: Path) -> None:
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_overflow import detect_raw_log_breaks

    raw_path = tmp_path / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b'{"ok":1}\n' + b"\x00" * 4096)

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CODEX)

    assert breaks[0].kind == "NUL_BYTES"


def test_two_agents_never_share_one_graded_capture() -> None:
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_unit_id_for

    headless_claude = AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.CLAUDE,
    )
    kimi = AgentConfig(
        cmd="kimi",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.GENERIC,
    )
    ccs_alias = AgentConfig(
        cmd="ccs glm",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.GENERIC,
    )
    other_ccs_alias = AgentConfig(
        cmd="ccs mm",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.GENERIC,
    )

    assert raw_log_unit_id_for(headless_claude).startswith("claude-headless-")
    assert raw_log_unit_id_for(kimi).startswith("kimi-")
    assert raw_log_unit_id_for(ccs_alias).startswith("ccs-glm-")
    assert raw_log_unit_id_for(other_ccs_alias).startswith("ccs-mm-")
    identities = {
        raw_log_unit_id_for(config)
        for config in (headless_claude, kimi, ccs_alias, other_ccs_alias)
    }
    assert len(identities) == 4


def test_an_evicted_queue_line_still_reaches_the_capture() -> None:
    from ralph.agents.invoke import BoundedLinesQueue

    evicted: list[str] = []
    queue = BoundedLinesQueue(maxlen=2)
    queue.set_eviction_sink(evicted.append)

    queue.append("one")
    queue.append("two")
    queue.append("three")
    queue.extend(["four", "five"])

    assert evicted == ["one", "two", "three"]
    assert queue.snapshot() == ["four", "five"]
    assert len(evicted) + len(queue.snapshot()) == 5, "no line may vanish"


def test_an_agent_invoked_by_path_keeps_its_identity() -> None:
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_unit_id_for

    by_path = AgentConfig(
        cmd="/opt/homebrew/bin/claude -p",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.CLAUDE,
    )
    ccs_by_path = AgentConfig(
        cmd="/usr/local/bin/ccs glm",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.GENERIC,
    )

    assert raw_log_unit_id_for(by_path).startswith("claude-headless-")
    assert raw_log_unit_id_for(ccs_by_path).startswith("ccs-glm-")


def test_a_codex_subcommand_is_not_treated_as_a_dispatcher_alias() -> None:
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_unit_id_for

    codex = AgentConfig(cmd="codex exec", output_flag="--json", transport=AgentTransport.CODEX)

    assert raw_log_unit_id_for(codex).startswith("codex-")


def test_the_nul_scan_is_lazy_by_construction() -> None:
    import inspect

    from ralph.display.raw_overflow import nul_separated_chunks

    assert inspect.isgenerator(nul_separated_chunks(b'{"ok":1}\n'))


def test_a_nul_run_spanning_window_boundaries_keeps_offsets_absolute() -> None:
    from ralph.display.raw_overflow import nul_separated_chunks

    window = 1 << 16
    head = b'{"ok":1}\n'
    for hole in (window - 1, window, window + 1, window * 2, window * 2 + 7):
        payload = head + b"\x00" * hole + b'{"after":1}\n'

        chunks = list(nul_separated_chunks(payload))

        assert [chunk for _, chunk in chunks] == [head, b'{"after":1}\n']
        assert chunks[1][0] == len(head) + hole, f"offset drifted for a {hole}-byte hole"


def test_a_payload_that_is_entirely_nul_yields_no_content() -> None:
    from ralph.display.raw_overflow import nul_separated_chunks

    chunks = list(nul_separated_chunks(b"\x00" * (1 << 17)))

    assert not any(chunk for _, chunk in chunks)
    assert len(chunks) <= 1, f"a NUL run must not yield a chunk per byte: {len(chunks)}"
