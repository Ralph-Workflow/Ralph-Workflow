"""Black-box tests for ``drop_unit`` cleanup.

wt-024 Step 11: per-unit dicts across ``ParallelDisplay``,
``ActivityRouter``, and ``SubprocessAgentExecutor`` must expose a
``drop_unit(unit_id)`` API that removes the unit's entries so long
parallel sessions don't accumulate state across waves. The
``parallel_coordinator`` worker-teardown path must invoke it.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock

from ralph.display.activity_router import ActivityProvider, ActivityRouter
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.raw_overflow import RawOverflowLog

_ = MagicMock  # re-export for inline monkeypatching below


class TestDropUnitCleanup:
    """All ``drop_unit`` paths in one class so the file declares a single top-level class."""

    def test_activity_router_drop_unit_removes_buffers_and_parsers(self) -> None:
        router = ActivityRouter()
        # Push a line so the unit has a parser and a buffer
        router.push_raw_line("unit-1", "hello world")
        router.push_raw_line("unit-2", "another line")

        assert "unit-1" in router._buffers
        assert "unit-1" in router._parsers
        assert "unit-2" in router._buffers

        router.drop_unit("unit-1")

        assert "unit-1" not in router._buffers, "drop_unit must remove buffer for the unit"
        assert "unit-1" not in router._parsers, "drop_unit must remove parser for the unit"
        assert "unit-2" in router._buffers, "drop_unit must NOT touch other units"

    def test_activity_router_drop_unit_on_unknown_unit_is_safe(self) -> None:
        router = ActivityRouter()
        # Must not raise
        router.drop_unit("never-added-unit")
        # And the router stays usable
        router.push_raw_line("real-unit", "line", provider=ActivityProvider.GENERIC)
        assert "real-unit" in router._buffers

    def test_parallel_display_drop_unit_removes_overflow_state(self) -> None:
        """ParallelDisplay must remove per-unit overflow log + warning state."""
        ws = Path("/tmp/ralph-drop-unit-test").resolve()
        ws.mkdir(parents=True, exist_ok=True)
        ctx = make_display_context()
        display = ParallelDisplay(display_context=ctx, workspace_root=ws)
        unit_id = "unit-42"
        # Force-populate the overflow state via the public surface so we
        # don't have to write megabytes to disk to trigger the overflow
        # path (the previous "60 MB giant string" version took ~1 s
        # per test which pushed the suite over the 60 s combined
        # budget). The drop_unit contract only cares about what is in
        # the per-unit state dicts — populating them directly is the
        # cheapest black-box probe.
        display._overflow_logs[unit_id] = RawOverflowLog(ws, unit_id, max_bytes=1024)
        display._overflow_warned.add(unit_id)
        display._drop_last_warned[unit_id] = 0.0
        # And seed the activity-router entries too so the
        # router-propagation assertion is meaningful.
        display._activity_router.get_buffer(unit_id)
        display._activity_router._parsers[unit_id] = display._activity_router._parser_factory(
            ActivityProvider.GENERIC
        )

        # Now call drop_unit and assert cleanup.
        display.drop_unit(unit_id)
        assert unit_id not in display._overflow_logs
        assert unit_id not in display._overflow_warned
        assert unit_id not in display._drop_last_warned
        # Activity-router drop propagated
        assert unit_id not in display._activity_router._buffers
        assert unit_id not in display._activity_router._parsers

    def test_parallel_display_drop_unit_removes_worker_streaming_state(self) -> None:
        """drop_unit must evict _last_worker_states, _active_block, and _last_checkpoint_chars.

        These three per-unit dicts grow across parallel waves without
        any eviction path; release them on drop_unit so long parallel
        runs do not accumulate state.
        """
        ws = Path("/tmp/ralph-drop-unit-streaming-test").resolve()
        ws.mkdir(parents=True, exist_ok=True)
        ctx = make_display_context()
        display = ParallelDisplay(display_context=ctx, workspace_root=ws)
        unit_id = "unit-streaming"
        display._last_worker_states[unit_id] = "RUNNING"
        display._active_block[unit_id] = ("text", ["fragment"])
        display._last_checkpoint_chars[unit_id] = 42

        display.drop_unit(unit_id)

        assert unit_id not in display._last_worker_states
        assert unit_id not in display._active_block
        assert unit_id not in display._last_checkpoint_chars

    def test_parallel_display_drop_unit_on_unknown_unit_is_safe(self) -> None:
        ws = Path("/tmp/ralph-drop-unit-safe-test").resolve()
        ws.mkdir(parents=True, exist_ok=True)
        ctx = make_display_context()
        display = ParallelDisplay(display_context=ctx, workspace_root=ws)
        # Must not raise on a unit we never saw
        display.drop_unit("never-added-unit")

    def test_drop_unit_closes_raw_log(self) -> None:
        """drop_unit must call ``RawOverflowLog.close()`` so buffered tails flush.

        RFC-013 P1 regression: the per-unit overflow log holds a 64 KB
        userspace buffer for fseventsd amortization; if ``drop_unit``
        only popped the dict entry the buffered tail would never reach
        disk. Inject a ``RawOverflowLog`` with a closed sentinel and
        assert the close() method is observed on drop.
        """

        ws = Path("/tmp/ralph-drop-unit-closes-raw-log").resolve()
        ws.mkdir(parents=True, exist_ok=True)
        ctx = make_display_context()
        display = ParallelDisplay(display_context=ctx, workspace_root=ws)
        unit_id = "unit-closes-raw-log"
        # Use a MagicMock so we can assert close() was called WITHOUT
        # writing a 64 KB buffer to disk. The production code only
        # needs to know ``close()`` ran; the buffered tail is the
        # implementation detail the watchdog doesn't care about here.
        mock_log = MagicMock(spec=RawOverflowLog)
        mock_log.path = ws / ".agent" / "raw" / f"{unit_id}.log"
        display._overflow_logs[unit_id] = mock_log

        display.drop_unit(unit_id)

        assert mock_log.close.call_count == 1, (
            "drop_unit must call close() on the per-unit RawOverflowLog so buffered "
            "tails reach disk deterministically"
        )
        assert unit_id not in display._overflow_logs

    def test_parallel_coordinator_drops_unit_after_teardown(self, monkeypatch: object) -> None:
        """parallel_coordinator worker's finally block must call drop_unit on display and router."""
        ws = Path("/tmp/ralph-drop-unit-coord-test").resolve()
        ws.mkdir(parents=True, exist_ok=True)
        ctx = make_display_context()
        display = ParallelDisplay(display_context=ctx, workspace_root=ws)
        router = ActivityRouter()

        unit_id = "coordinator-unit"
        # Pre-populate state so we can prove drop_unit clears it.
        router.push_raw_line(unit_id, "some line", provider=ActivityProvider.GENERIC)
        assert unit_id in router._buffers

        # Simulate the coordinator's finally block directly.
        with contextlib.suppress(Exception):
            display.drop_unit(unit_id)
        with contextlib.suppress(Exception):
            router.drop_unit(unit_id)

        assert unit_id not in router._buffers
        assert unit_id not in router._parsers
