from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from ralph.mcp.server._transport_repetition_tracker import (
    REPETITION_STATE_FILENAME,
    TransportRepetitionTracker,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]


def _child_writer_observer(
    snapshot_dir: Path,
    signature: str,
    count: int,
) -> None:
    import os

    os.environ["RALPH_MCP_SNAPSHOT_DIR"] = str(snapshot_dir)
    tracker = TransportRepetitionTracker.from_parent_owned_state()
    if tracker.state_path is None:
        raise RuntimeError("TransportRepetitionTracker.state_path must be set in subprocess")
    for _ in range(count):
        tracker.observe(signature)


def _read_carried_streak(state_path: Path) -> int:
    if not state_path.exists():
        return 0
    raw = state_path.read_text(encoding="utf-8")
    if not raw.strip():
        return 0
    decoded: object = json.loads(raw)
    if not isinstance(decoded, dict):
        return 0
    streak = decoded.get("streak")
    if isinstance(streak, bool) or not isinstance(streak, int):
        return 0
    return streak


def _drive_two_children(
    tmp_path: Path,
    *,
    count_per_child: int,
    signature: str,
) -> tuple[int, int]:
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    state_path = snapshot_dir / REPETITION_STATE_FILENAME
    assert state_path.parent.exists()

    ctx = multiprocessing.get_context("fork")
    p1 = ctx.Process(
        target=_child_writer_observer,
        args=(snapshot_dir, signature, count_per_child),
    )
    p2 = ctx.Process(
        target=_child_writer_observer,
        args=(snapshot_dir, signature, count_per_child),
    )
    p1.start()
    p2.start()
    p1.join(timeout=10.0)
    p2.join(timeout=10.0)

    assert p1.exitcode == 0, f"child A exited with code {p1.exitcode}"
    assert p2.exitcode == 0, f"child B exited with code {p2.exitcode}"

    observations_made = 2 * count_per_child
    carried_streak = _read_carried_streak(state_path)
    return carried_streak, observations_made


def test_e16_transport_sidecar_two_child_interleave_preserves_observations(
    tmp_path: Path,
) -> None:
    signature = "loop-sig-wu3"
    carried_streak, observations_made = _drive_two_children(
        tmp_path, count_per_child=4, signature=signature
    )
    assert carried_streak == observations_made, (
        f"transport-repetition sidecar lost observations: carried={carried_streak}, "
        f"expected={observations_made}; the protocol's lock is broken"
    )


def test_e16_transport_sidecar_two_child_interleave_extends_after_carry_over(
    tmp_path: Path,
) -> None:
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    state_path = snapshot_dir / REPETITION_STATE_FILENAME
    signature = "carry-sig-wu3"
    first_obs = 3
    second_obs = 2
    expected_total = first_obs + second_obs

    ctx = multiprocessing.get_context("fork")

    p1 = ctx.Process(
        target=_child_writer_observer,
        args=(snapshot_dir, signature, first_obs),
    )
    p1.start()
    p1.join(timeout=10.0)
    assert p1.exitcode == 0, f"first child exited with code {p1.exitcode}"

    p2 = ctx.Process(
        target=_child_writer_observer,
        args=(snapshot_dir, signature, second_obs),
    )
    p2.start()
    p2.join(timeout=10.0)
    assert p2.exitcode == 0, f"second child exited with code {p2.exitcode}"

    carried_streak = _read_carried_streak(state_path)
    assert carried_streak == expected_total, (
        f"second child dropped the carried streak: carried={carried_streak}, "
        f"expected={expected_total}; the cross-process read+merge under flock broke"
    )
