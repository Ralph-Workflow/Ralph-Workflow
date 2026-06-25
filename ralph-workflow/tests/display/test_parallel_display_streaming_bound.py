"""Black-box tests for ParallelDisplay streaming-block fragment bound.

wt-024 memory-perf GAP-MEM-03: the streaming-block fragment list
grows without bound inside a single streaming block, and every
checkpoint recomputes ``sum(len(x) for x in accumulated)`` plus a full
``' '.join(accumulated)`` — O(n^2) on pathological chatty streams.

The fix is a close-and-reopen cap (``_MAX_STREAMING_FRAGMENTS``) plus a
running char total (``_active_block_chars``) declared in ``__slots__``,
so checkpoint char math is no longer O(n).

These tests exercise the cap and the running-total invariant. They
disable BOTH streaming dedup AND streaming checkpoints via env (so the
fragment list grows and no checkpoint console-printing occurs) and use
distinct content per iteration (so dedup cannot mask growth).
"""

from __future__ import annotations

from pathlib import Path

from ralph.display._streaming_ctx import _StreamingCtx
from ralph.display.context import make_display_context
from ralph.display.parallel_display import (
    _MAX_STREAMING_FRAGMENTS,
    ParallelDisplay,
)


def test_streaming_fragment_list_is_bounded() -> None:
    """``_continue_streaming_block`` must keep the fragment list at or below the cap.

    Without the cap, a chatty single-block stream grows the fragment
    list without bound. With the cap, the list stays at or below
    ``_MAX_STREAMING_FRAGMENTS`` because the close-and-reopen path
    flushes and reopens the block once the cap is hit.

    The running-total invariant ``_active_block_chars[unit_id] ==
    sum(len(x) for x in accumulated)`` must hold at every moment of
    the open block (the running total replaces the O(n) ``sum()``
    recomputation on every checkpoint).
    """
    ws = Path("/tmp/ralph-streaming-bound-test").resolve()
    ws.mkdir(parents=True, exist_ok=True)
    ctx = make_display_context(
        env={"RALPH_STREAMING_DEDUP": "0", "RALPH_STREAMING_CHECKPOINTS": "0"}
    )
    display = ParallelDisplay(display_context=ctx, workspace_root=ws)
    display._active_block["u1"] = ("text", [])

    start_tag = "start"
    continue_tag = "cont"
    for i in range(_MAX_STREAMING_FRAGMENTS + 50):
        sctx = _StreamingCtx(
            unit_id="u1",
            kind="text",
            content=f"frag-{i}",
            base_tag="text",
            timestamp="",
        )
        display._continue_streaming_block(
            sctx, display._active_block["u1"][1], continue_tag, start_tag
        )

    accumulated = display._active_block["u1"][1]
    assert len(accumulated) <= _MAX_STREAMING_FRAGMENTS, (
        f"fragment list exceeded cap: len={len(accumulated)} > {_MAX_STREAMING_FRAGMENTS}"
    )
    assert display._active_block_chars["u1"] == sum(len(x) for x in accumulated), (
        "running char total drifted from sum(len(x) for x in accumulated)"
    )
