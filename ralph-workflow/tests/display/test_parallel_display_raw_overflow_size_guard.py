"""Regression tests for the raw-overflow size-guard wiring.

wt-029-fs-opti Step 11: parser-failure / raw malformed-line overflow
must inherit the same size-guard + one-shot warning logic the
condensed-content path uses. Pre-fix, ``_raw_overflow_write`` only
called ``RawOverflowLog.append`` and never routed through
``_check_overflow_size``, so the 50 MB cap could be hit silently and
the ``[overflow log full ...]`` warning the operator relies on was
never emitted.

These tests drive ``_raw_overflow_write`` past the byte cap and
assert the warning contract via the ``_overflow_warned`` set (the
debouncing state that lives on ``ParallelDisplay``) plus the
``RawOverflowLog.is_disabled`` flag. A captured StringIO Console
also asserts the ``[overflow log full ...]`` text reaches the
operator-facing output stream.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import ralph.display.parallel_display as pd_module
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.raw_overflow import RawOverflowLog


@pytest.fixture
def display_with_small_cap(tmp_path: Path) -> tuple[ParallelDisplay, StringIO]:
    """Build a ParallelDisplay with a tiny overflow cap and a Console capture.

    Yields ``(display, captured_stream)``. The captured stream holds
    whatever ``emit_activity_line`` routed to the rich Console. Tests
    parse it for the ``[overflow log full ...]`` substring to verify
    the one-shot warning reaches the operator.

    The fixture shrinks ``_MAX_OVERFLOW_FILE_BYTES`` to ``16`` for
    the duration of the test so a few KB of writes trip the size
    guard without burning seconds on disk I/O, then restores it
    on teardown so no other test sees the shrunken cap.
    """
    original_cap = pd_module._MAX_OVERFLOW_FILE_BYTES
    pd_module._MAX_OVERFLOW_FILE_BYTES = 16
    try:
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, color_system=None, width=240)
        ctx = make_display_context(console=console, env={})
        display = ParallelDisplay(display_context=ctx, workspace_root=tmp_path)
        display._workspace_root = tmp_path
        yield display, buf
    finally:
        pd_module._MAX_OVERFLOW_FILE_BYTES = original_cap


def test_raw_overflow_write_emits_one_shot_warning_when_cap_exceeded(
    display_with_small_cap: tuple[ParallelDisplay, StringIO],
) -> None:
    """Driving ``_raw_overflow_write`` past the cap emits the one-shot warning.

    Regression for the analysis-feedback finding: pre-fix the raw
    path never routed through ``_check_overflow_size`` so the
    warning was silent. The fix threads ``_check_overflow_size``
    into ``_raw_overflow_write`` so the parser-failure path
    inherits the condensed-content branch's size-guard semantics.
    """
    display, buf = display_with_small_cap
    unit_id = "raw-overflow-unit"

    # Each line is "<chars>\\n"; a 7-char line is 8 bytes on disk.
    display._raw_overflow_write(unit_id, "1234567")  # 8 bytes
    display._raw_overflow_write(unit_id, "abcdefg")  # 16 bytes -- at cap
    display._raw_overflow_write(unit_id, "overflow")  # would exceed

    overflow = display._overflow_logs[unit_id]
    assert overflow.is_disabled, "overflow log must be disabled after the byte cap is reached"
    assert unit_id in display._overflow_warned, (
        "unit_id must be in _overflow_warned so the one-shot debouncing fires"
    )

    output = buf.getvalue()
    assert "overflow log full" in output, (
        f"operator-facing warning missing from rendered output: {output!r}"
    )
    assert unit_id in output, f"unit_id missing from warning: {output!r}"


def test_raw_overflow_write_warning_idempotent(
    display_with_small_cap: tuple[ParallelDisplay, StringIO],
) -> None:
    """Repeated writes after the cap emits the warning only ONCE.

    The debouncing contract: ``_overflow_warned`` is a per-unit set;
    once a unit has been warned, subsequent cap-exceeding writes
    must NOT re-emit the warning. This is the existing
    ``_check_overflow_size`` contract, and the regression pins the
    wiring on the raw path.
    """
    display, buf = display_with_small_cap
    unit_id = "raw-overflow-unit"

    for _ in range(5):
        display._raw_overflow_write(unit_id, "1234567")
        display._raw_overflow_write(unit_id, "abcdefg")
        display._raw_overflow_write(unit_id, "overflow")

    output = buf.getvalue()
    count = output.count("overflow log full")
    assert count == 1, f"warning must be one-shot; got {count} emissions in output: {output!r}"
    assert unit_id in display._overflow_warned


def test_check_overflow_size_uses_size_bytes_counter(
    tmp_path: Path,
    display_with_small_cap: tuple[ParallelDisplay, StringIO],
) -> None:
    """``_check_overflow_size`` checks the in-memory counter, NOT a stat() probe.

    Regression: pre-fix the function used ``overflow.path.stat().st_size``
    which depends on the 5 s flush interval. The fix uses
    ``overflow.size_bytes`` (the authoritative in-memory counter) so
    the warning fires on the first cap-crossing append rather than
    waiting for the next flush. This test asserts the in-memory
    contract: a log whose in-memory counter is at the cap (but whose
    on-disk file size is 0 because nothing has flushed) MUST trigger
    the warning.
    """
    display, buf = display_with_small_cap
    unit_id = "size-bytes-unit"
    overflow = RawOverflowLog(tmp_path, unit_id, max_bytes=16, flush_interval_seconds=3600.0)
    display._overflow_logs[unit_id] = overflow

    # Append enough bytes to be at the cap WITHOUT flushing; the on-disk
    # file size must be 0 while size_bytes is at the cap. This proves
    # the in-memory counter is the authoritative source.
    overflow.append("1234567")  # 8 bytes buffered, not flushed
    overflow.append("abcdefg")  # 16 bytes buffered, not flushed
    assert overflow.size_bytes == 16
    on_disk_size = overflow.path.stat().st_size
    assert on_disk_size == 0, (
        f"no flush has happened yet, on-disk size must be 0; got {on_disk_size}"
    )

    # Now run the size check.
    display._check_overflow_size(unit_id, overflow)

    output = buf.getvalue()
    assert "overflow log full" in output, (
        "in-memory counter above cap must trigger the warning even when "
        f"the on-disk file is empty; got output: {output!r}"
    )
    assert overflow.is_disabled
    assert unit_id in display._overflow_warned


def test_check_overflow_size_emits_for_already_disabled_log(
    tmp_path: Path,
    display_with_small_cap: tuple[ParallelDisplay, StringIO],
) -> None:
    """An auto-disabled log (cap reached mid-write) still emits the warning.

    The contract: ``RawOverflowLog.append`` auto-disables the log
    when a single line would push the cap. The follow-up
    ``_check_overflow_size`` call must STILL emit the one-shot
    warning so the operator learns the cap was hit. Without this,
    parser-failure overflow could silently stop at the cap.
    """
    display, buf = display_with_small_cap
    unit_id = "auto-disabled-unit"
    overflow = RawOverflowLog(tmp_path, unit_id, max_bytes=8, flush_interval_seconds=3600.0)
    display._overflow_logs[unit_id] = overflow

    # First line: succeeds. Second line: would push over the 8-byte
    # cap, so append() auto-disables and returns False.
    assert overflow.append("1234567") is True
    assert overflow.append("toolongtofit") is False
    assert overflow.is_disabled is True

    # Check the size via the display; this is the same call the
    # ``_raw_overflow_write`` path makes after each append.
    display._check_overflow_size(unit_id, overflow)

    output = buf.getvalue()
    assert "overflow log full" in output, (
        f"auto-disabled log must still surface the warning; got output: {output!r}"
    )
    assert unit_id in display._overflow_warned


def test_emit_activity_event_warns_when_append_disables_overflow(
    display_with_small_cap: tuple[ParallelDisplay, StringIO],
) -> None:
    """Regression for the analysis-feedback finding on the condensed path.

    Pre-fix, ``_emit_activity_event`` called ``_check_overflow_size``
    BEFORE ``overflow.append(text)``. When ``RawOverflowLog.append``
    itself auto-disabled the log (the byte cap was reached mid-write),
    the size check ran against a log whose in-memory ``size_bytes``
    counter was still below the cap, so no warning was emitted even
    though raw capture was being silently dropped.

    The fix swaps the order: ``overflow.append(text)`` first, then
    ``_check_overflow_size`` so the post-append state (including the
    auto-disabled flag) is observed. This test asserts all three
    contracts the operator relies on:

    1. ``RawOverflowLog.is_disabled`` is ``True`` (append disabled it)
    2. ``unit_id`` is in ``display._overflow_warned`` (debouncing fires)
    3. The operator-facing output contains the ``[overflow log full ...]``
       warning text the operator keys off of
    """
    display, buf = display_with_small_cap
    unit_id = "condensed-overflow-unit"

    # A tool_result large enough to force ``condense_content`` to mark
    # the output as condensed (and therefore route through the
    # overflow.append branch) but bigger than the tiny ``max_bytes=16``
    # cap so the append itself auto-disables the log.
    soft_limit = display._ctx.condenser_soft_limit
    oversized_text = "X" * (max(soft_limit, 32) + 64)

    display._emit_activity_event(
        unit_id,
        ActivityEventKind.TOOL_RESULT,
        oversized_text,
        None,
    )

    overflow = display._overflow_logs.get(unit_id)
    assert overflow is not None, "the condensed branch must have created a per-unit overflow log"
    assert overflow.is_disabled, (
        "append() must auto-disable when the single-line byte budget is exceeded"
    )
    assert unit_id in display._overflow_warned, (
        "unit_id must be in _overflow_warned so the one-shot debouncing fires"
    )

    output = buf.getvalue()
    assert "overflow log full" in output, (
        f"operator-facing warning missing from rendered output: {output!r}"
    )
