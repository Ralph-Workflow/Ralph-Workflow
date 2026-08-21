"""Unit tests for the RawOverflowLog class."""

from __future__ import annotations

import io
import threading
import weakref
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.display.raw_overflow import RawOverflowLog

if TYPE_CHECKING:
    import pytest


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
    """Each run starts a clean capture."""
    from ralph.display.raw_overflow import reset_raw_overflow_path_state

    log1 = RawOverflowLog(tmp_path, "unit-1")
    log1.append("run1 line")
    log1.close()

    reset_raw_overflow_path_state()  # a new run

    log2 = RawOverflowLog(tmp_path, "unit-1")
    log2.append("run2 line")
    log2.flush()

    content = log2.path.read_text(encoding="utf-8")
    assert "run1 line" not in content
    assert "run2 line" in content
    log2.close()


def test_a_later_writer_in_one_run_continues_the_file(tmp_path: Path) -> None:
    """Within a run, a second writer for a path must not erase the first.

    A writer is per-acquisition: ``drop_unit`` forgets a unit's log, the
    readers build one per agent invocation, and the weak registry evicts
    an instance once nothing holds it. Truncating on each acquisition
    left a run's transcript holding only its last invocation, and erased
    condensed bodies the rendered records still point at by path.
    """
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
    """The cap belongs to the FILE, not to whichever writer holds it.

    Resetting the byte count on each acquisition let a run exceed the
    ceiling arbitrarily by re-acquiring the log.
    """
    log1 = RawOverflowLog(tmp_path, "unit-1", max_bytes=40)
    log1.append("x" * 30)
    log1.close()

    log2 = RawOverflowLog(tmp_path, "unit-1", max_bytes=40)

    # ``size_bytes`` is this writer's own count, not the file's.
    assert log2.size_bytes == 0
    # ...but the cap still knows about the 31 bytes already on disk.
    assert log2.append("y" * 30) is False
    assert log2.is_disabled


def test_unit_id_sanitization(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit/with:special chars!")
    log.append("test")
    log.flush()
    # S-23 (wt-028-display): the verbatim capture derives its id from
    # ``safe_id_for(unit_id, model)`` so it pairs with the rendered
    # record. ``safe_id_for`` strips leading AND trailing underscores,
    # so the trailing ``_`` from the historical sanitizer is gone.
    assert log.path.name == "unit_with_special_chars.log"
    assert log.path.exists()
    log.close()


def test_unit_id_with_model_pairs_with_rendered_record(tmp_path: Path) -> None:
    """S-23 (wt-028-display): the verbatim capture and the rendered record
    share the same ``safe_id_for(agent, model)`` id so condensation
    markers in the rendered record point at a real file on disk.
    """
    from ralph.display.record_writer import rendered_record_path, safe_id_for

    log = RawOverflowLog(tmp_path, "pi", model="minimax-MiniMax-3")
    log.append("verbatim line")
    log.flush()
    rendered = rendered_record_path(tmp_path, "pi", model="minimax-MiniMax-3")
    # Both files share the same ``safe_id_for`` id; the verbatim
    # capture adds ``.log`` and the rendered record adds ``.rendered.log``.
    shared_id = safe_id_for("pi", "minimax-MiniMax-3")
    assert log.path.name == f"{shared_id}.log"
    assert rendered.name == f"{shared_id}.rendered.log"
    # The two files have the same parent directory -- the rendered
    # record's condensation markers can point at the verbatim capture
    # by relative path (``safe_id_for(pi, model).log``).
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
    # Create a file where the .agent/raw directory should be
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    raw_file = agent_dir / "raw"
    raw_file.write_text("not a directory", encoding="utf-8")

    log = RawOverflowLog(tmp_path, "unit-1")
    # Should not raise even though the path is a file, not a directory
    log.append("test line")
    # Black-box check: the per-unit log file should not exist as a regular file
    # since mkdir failed; the append silently no-oped.
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

    assert log.append("1234567") is True  # 8 bytes with trailing newline
    assert log.append("abcdefg") is True  # 8 bytes with trailing newline
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
    assert expected == log._bytes_written
    assert log.size_bytes == expected
    assert log.size_bytes == log._bytes_written
    log.close()


def test_size_bytes_returns_bytes_written_when_disabled(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("first line")
    log.disable()
    assert log.size_bytes == log._bytes_written


def test_size_bytes_authoritative_even_when_file_unlinked_after_write(
    tmp_path: Path,
) -> None:
    """Watchdog liveness contract: size_bytes must advance on every append,
    independent of on-disk visibility. A file that an operator, a sanitizer,
    or a chmod-000 directory just unlinked must NOT zero the counter back.
    """
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("some content")
    log.flush()
    expected = len(b"some content\n")
    assert log.path.exists()
    log.path.unlink()
    assert not log.path.exists()
    # The in-memory counter is the authoritative liveness signal;
    # the watchdog must still see growth.
    assert log.size_bytes == expected
    assert log.size_bytes == log._bytes_written
    log.close()


def test_size_bytes_returns_zero_when_prior_run_file_exists(tmp_path: Path) -> None:
    """``size_bytes`` counts what THIS writer wrote, never the file.

    The idle watchdog's log-growth probe reads this and treats a nonzero
    value as "this invocation has produced output", so a writer that
    inherits an earlier invocation's bytes must still start at zero.
    """
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


# New tests for buffered handle, time-based flush, and explicit close().


def test_append_keeps_handle_open_and_buffers(tmp_path: Path) -> None:
    """Writes are buffered; flush() makes them visible on disk."""
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=3600.0)
    log.append("buffered line")
    # size_bytes must track appends immediately (watchdog liveness contract)
    assert log.size_bytes == len(b"buffered line\n")
    log.flush()
    assert "buffered line\n" in log.path.read_text(encoding="utf-8")
    log.close()


def test_time_based_flush(tmp_path: Path) -> None:
    fake_time = [0.0]
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=5.0, now=lambda: fake_time[0])
    log.append("first")
    fake_time[0] = 6.0
    log.append("second")  # crosses the interval -> flush
    log.close()
    content = log.path.read_text(encoding="utf-8")
    assert "first\n" in content
    assert "second\n" in content


def test_close_flushes_and_reopen_appends(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=3600.0)
    log.append("before close")
    log.close()
    assert "before close\n" in log.path.read_text(encoding="utf-8")
    log.append("after close")  # reopens in append mode
    log.close()
    content = log.path.read_text(encoding="utf-8")
    assert "before close\n" in content
    assert "after close\n" in content


def test_close_is_idempotent(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("x")
    log.close()
    log.close()  # no raise


def test_executor_drop_unit_closes_raw_log(tmp_path: Path) -> None:
    """SubprocessAgentExecutor.drop_unit() must close the raw log and
    flush its buffered tail to disk (RFC-013 P1)."""
    executor = SubprocessAgentExecutor.__new__(SubprocessAgentExecutor)
    executor._raw_logs = {}
    executor._raw_overflow_root = tmp_path
    executor._cwd = tmp_path
    log = executor._get_raw_log("unit-x")
    log.append("pending line")
    executor.drop_unit("unit-x")
    # close() during drop must have flushed the buffered tail
    assert "pending line\n" in (tmp_path / ".agent" / "raw" / "unit-x.log").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# DA-001: registry teardown closes buffered handles before finalization.
#
# Pre-fix: ``_REGISTRY`` held strong ``dict`` references to every
# ``RawOverflowLog`` ever constructed, so a test or run that ended
# without an explicit ``drop_unit`` / ``stop()`` reached interpreter
# finalization with the buffered file handle still open. ``python -W
# error::ResourceWarning`` (the ``make verify`` invariant) raised
# ``ResourceWarning: unclosed file ...`` for every such case.
#
# Post-fix: the registry is a ``WeakValueDictionary`` and each instance
# registers a ``weakref.finalize`` callback that closes the handle
# before the instance is collected. A test that lets go of its last
# strong reference MUST NOT leave the buffered handle open.
# ---------------------------------------------------------------------------


def test_weakref_finalize_closes_handle_on_gc(tmp_path: Path) -> None:
    """An orphaned ``RawOverflowLog`` closes its handle when collected.

    Constructs an instance via ``RawOverflowLog(...)`` directly (i.e.
    the pre-fix-style direct constructor that bypasses the registry),
    appends a line to force the buffered handle to open, drops the
    last strong reference, and asserts the handle is closed after
    ``gc.collect()`` -- without ever calling ``close()`` /
    ``drop_unit`` / ``stop()``.
    """
    import gc
    import weakref

    log = RawOverflowLog(tmp_path, "u1")
    assert log.append("survive until GC\n") is True
    log.flush()
    # The handle is now open and buffered. Confirm the buffered
    # writer is live (it must be, since ``append`` returned True).
    assert log._fh is not None
    assert not log._fh.closed

    # Capture a WEAK reference so we can read the post-finalize
    # state without keeping the instance alive ourselves.
    weak_log: weakref.ref[RawOverflowLog] = weakref.ref(log)
    path = log.path
    # Drop the only strong reference. Without the finalize hook the
    # interpreter would emit ``ResourceWarning: unclosed file`` at
    # finalization; with the hook, the close runs first.
    del log
    gc.collect()

    # The instance has been collected; ``weak_log()`` resolves to
    # None. Confirm the handle's on-disk file is intact (the append
    # before finalization must have flushed), and confirm a fresh
    # registry call returns a NEW instance -- proving the previous
    # one was actually reaped.
    assert weak_log() is None, (
        "the instance must be garbage-collected after dropping the "
        "last strong reference"
    )
    on_disk = path.read_bytes()
    assert b"survive until GC\n" in on_disk, (
        "the buffered payload must have hit disk before finalization"
    )
    fresh = RawOverflowLog(tmp_path, "u1")
    try:
        assert fresh is not None
        assert fresh._fh is None
    finally:
        fresh.close()


def test_registry_holds_weak_reference_only(tmp_path: Path) -> None:
    """The per-path registry must NOT keep instances alive on its own.

    Pre-fix: a strong ``dict`` entry kept the instance reachable
    across test boundaries, leaking the buffered handle past the
    test's lifetime. Post-fix: the registry is a
    ``WeakValueDictionary`` whose strong reference count on the
    stored value is zero; once the caller releases its reference,
    a subsequent ``get_or_create_raw_overflow_log`` returns a NEW
    instance (the old one was reaped).
    """
    import gc

    from ralph.display.raw_overflow import get_or_create_raw_overflow_log

    first = get_or_create_raw_overflow_log(tmp_path, "u-weak-registry")
    first.append("register check\n")
    first.flush()

    # Drop the only strong reference and force collection. A second
    # lookup MUST return a fresh instance, not the same one.
    weak_first: weakref.ref[RawOverflowLog] = weakref.ref(first)
    del first
    gc.collect()
    assert weak_first() is None, (
        "DA-001 invariant: the previous instance must be reaped "
        "after the caller drops its reference"
    )

    second = get_or_create_raw_overflow_log(tmp_path, "u-weak-registry")
    assert second is not None
    # A second append must succeed (i.e. the instance is fresh, the
    # file handle is closed, no stale state blocks the write).
    assert second.append("after-reap\n") is True
    second.flush()
    on_disk = second.path.read_bytes()
    assert b"after-reap\n" in on_disk
    second.close()


def test_close_all_raw_overflow_logs_closes_all_handles(tmp_path: Path) -> None:
    """Explicit teardown via ``close_all_raw_overflow_logs()`` closes
    every registered handle, even when the caller has not yet dropped
    its own strong reference."""
    from ralph.display.raw_overflow import (
        close_all_raw_overflow_logs,
        get_or_create_raw_overflow_log,
    )

    log_a = get_or_create_raw_overflow_log(tmp_path, "u-a")
    log_b = get_or_create_raw_overflow_log(tmp_path, "u-b")
    # Force the buffered handle open on each.
    assert log_a.append("a\n") is True
    assert log_b.append("b\n") is True

    close_all_raw_overflow_logs()

    # The buffered payload must have hit disk before the close.
    a_on_disk = log_a.path.read_bytes()
    b_on_disk = log_b.path.read_bytes()
    assert b"a\n" in a_on_disk
    assert b"b\n" in b_on_disk

    # A subsequent append on the now-disabled instance must be a
    # no-op (the close_all path also called ``disable()``).
    log_a_fh = log_a._fh
    log_b_fh = log_b._fh
    assert log_a_fh is None or log_a_fh.closed, (
        "close_all_raw_overflow_logs must close every buffered handle"
    )
    assert log_b_fh is None or log_b_fh.closed

    log_a.close()
    log_b.close()


def test_long_thinking_emits_one_close_line_no_checkpoints_handle_closed(
    tmp_path: Path,
) -> None:
    """Regression for the measured 2026-08-06 unclosed-handle finding.

    ``test_long_thinking_emits_one_close_line_no_checkpoints`` (in
    ``test_display_thinking_preview_reproduction_thinking_preview_and_transcript_cleanup.py``)
    exercises the thinking-block close path with a payload large
    enough to trigger ``condensed_flag`` -- the condenser writes the
    verbatim block to the raw overflow log via ``overflow.append()``,
    opening a buffered file handle. Pre-fix, the test never called
    ``drop_unit`` / ``stop()`` and the handle leaked past finalization,
    triggering ``ResourceWarning`` under ``-W error::ResourceWarning``.

    This regression constructs the same scenario directly (a
    ``ParallelDisplay`` whose thinking block exceeds the condenser
    soft limit, no explicit stop) and asserts the buffered payload
    hits disk (so the close happened with the buffered bytes still
    intact) and a subsequent ``get_or_create_raw_overflow_log``
    returns a fresh instance -- proving the previous one was reaped.
    """
    import gc
    import weakref

    from rich.console import Console

    from ralph.display.activity_model import ActivityEventKind
    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.display.raw_overflow import get_or_create_raw_overflow_log

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

    # Confirm the fixture actually exercised the regression surface:
    # the raw overflow log was appended to, opening a buffered handle.
    # Force a flush first so the buffered bytes hit disk before we
    # release the strong references.
    # Condensed bodies are display-authored, so they land in the
    # ``.overflow.log`` sibling rather than the verbatim capture.
    overflow = pd._condensed_logs.get(unit_id)
    assert overflow is not None
    assert overflow._fh is not None and not overflow._fh.closed, (
        "fixture must trigger the raw-overflow append path so the "
        "buffered handle is open at this point"
    )
    overflow.flush()
    raw_path = overflow.path
    on_disk_during_run = raw_path.read_bytes()
    assert len(on_disk_during_run) > 0, (
        "the appended block must hit disk after flush"
    )

    # Capture a WEAK reference to the display's overflow log so we
    # can read post-finalize state without keeping the instance
    # alive. ``ParallelDisplay`` itself does not support ``weakref``
    # (no ``__weakref__`` slot), so we observe the lifecycle
    # indirectly via the per-unit overflow log and the registry.
    weak_overflow: weakref.ref[RawOverflowLog] = weakref.ref(overflow)

    # Release every strong reference to the display and the overflow
    # log. After ``gc.collect()`` the ``weakref.finalize`` hook must
    # have closed the handle, after which the ``WeakValueDictionary``
    # auto-evicts the entry on the next lookup.
    del pd, overflow, buf, console
    gc.collect()

    assert weak_overflow() is None, (
        "DA-001 invariant: the RawOverflowLog must be collected "
        "once its owning ParallelDisplay is unreachable"
    )
    # A subsequent lookup MUST return a fresh instance, not the
    # same one -- proving the registry entry vanished (the
    # ``WeakValueDictionary`` auto-eviction worked). The fresh
    # instance starts a new run and the first append truncates by
    # design (mode="wb"), so the post-reap file is just the new
    # append, not an extension of the old one. What matters is
    # that the append succeeded and the bytes hit disk.
    fresh = get_or_create_raw_overflow_log(tmp_path, unit_id, condensed=True)
    try:
        assert fresh is not None
        # The fresh instance must be writable (no stale handle from
        # the reaped one).
        assert fresh.append("post-reap\n") is True
        fresh.flush()
        on_disk_after = raw_path.read_bytes()
        assert b"post-reap\n" in on_disk_after, (
            "the post-reap append must extend the on-disk file"
        )
    finally:
        fresh.close()


def test_cap_warning_is_emitted_once_per_file(tmp_path: Path) -> None:
    """The 'log full' warning belongs to the file, not to each writer.

    A writer is per agent invocation, so a per-instance warning meant one
    WARNING per invocation for the rest of a run once a capture filled.
    """
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
    """The finalizer must close the handle, not merely be registered.

    It used to receive a weak reference to its owner. ``weakref.finalize``
    runs AFTER the referent is cleared, so that reference always resolved
    to ``None`` and the callback did nothing -- the handle stayed open
    until CPython's buffered-writer destructor reached it, and the
    ``ResourceWarning`` the hook exists to prevent came back.

    Asserting on the handle itself is the point: the previous tests
    checked only that GC had run and that flushed bytes were on disk,
    both of which stay true with the hook completely broken.
    """
    import gc

    log = RawOverflowLog(tmp_path, "unit-finalize")
    log.append("payload")
    handle_box = log._handle_box
    assert handle_box[0] is not None, "precondition: the handle is open"
    assert not handle_box[0].closed

    del log
    gc.collect()

    assert handle_box[0] is None, "the finalizer left the handle open"


def test_dropping_a_log_emits_no_resource_warning(tmp_path: Path) -> None:
    """The DA-001 contract, stated as the warning it exists to prevent."""
    import gc
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        log = RawOverflowLog(tmp_path, "unit-warn")
        log.append("payload")
        del log
        gc.collect()


def test_detection_is_bounded_on_a_badly_corrupted_capture(tmp_path: Path) -> None:
    """A corrupt capture must not cost more than the run it is grading.

    Unbounded, this measured 5.2 million break objects in 119 s and
    1.5 GB for a 10 MB file -- inside phase close, in exactly the case
    the detector exists for. Every consumer reads the first break; the
    rest was pure cost.
    """
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_overflow import MAX_REPORTED_BREAKS, detect_raw_log_breaks

    raw_path = tmp_path / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"not json\n" * 50_000)

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CODEX)

    assert len(breaks) == MAX_REPORTED_BREAKS
    assert breaks[0].kind == "NON_JSONL"


def test_a_nul_hole_yields_one_chunk_not_one_per_byte(tmp_path: Path) -> None:
    """A NUL hole is the measured corruption shape; it must stay cheap.

    Splitting the payload on every NUL allocated one object per byte
    before any grading ran, so the break cap could not bound it -- 7.9 s
    and 517 MB at the file cap, inside phase close. The invariant is
    structural, not timing: a run of NULs is ONE step, however long it
    is.
    """
    del tmp_path
    from ralph.display.raw_overflow import nul_separated_chunks

    payload = b'{"ok":1}\n' + b"\x00" * 100_000 + b'{"after":1}\n'

    chunks = list(nul_separated_chunks(payload))

    assert len(chunks) == 2, f"a NUL run must not yield a chunk per byte: {len(chunks)}"
    assert chunks[0][1] == b'{"ok":1}\n'
    assert chunks[1][1] == b'{"after":1}\n'
    # Offsets stay absolute so a reported break names a real byte.
    assert chunks[1][0] == len(b'{"ok":1}\n') + 100_000


def test_a_nul_hole_is_still_reported_as_corruption(tmp_path: Path) -> None:
    """Cheapness must not cost detection."""
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_overflow import detect_raw_log_breaks

    raw_path = tmp_path / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b'{"ok":1}\n' + b"\x00" * 4096)

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CODEX)

    assert breaks[0].kind == "NUL_BYTES"


def test_two_agents_never_share_one_graded_capture() -> None:
    """``--output-format=stream-json`` is not unique to Claude.

    kimi ships it as its default output flag and every ccs alias
    inherits it, so an ungated check filed those transcripts under
    ``claude-headless`` -- the same path headless Claude is graded on.
    One agent then grades another's corruption and quotes another's
    transport failures into its own verdict.
    """
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
        cmd="glm",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.GENERIC,
    )

    assert raw_log_unit_id_for(headless_claude) == "claude-headless"
    assert raw_log_unit_id_for(kimi) == "kimi"
    assert raw_log_unit_id_for(ccs_alias) == "glm"


def test_an_evicted_queue_line_still_reaches_the_capture() -> None:
    """The bounded queue must not be able to lose agent output silently.

    It drops its oldest entry when the producer outruns the consumer,
    and the capture is written consumer-side -- so a dropped line never
    reached the transcript. Measured before the sink: a 1000-line burst
    reached the consumer as 256, and the missing 744 left a file that
    still parsed, so nothing reported the loss.
    """
    from ralph.agents.invoke._bounded_lines_queue import BoundedLinesQueue

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
