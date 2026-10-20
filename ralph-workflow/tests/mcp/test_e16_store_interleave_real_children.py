"""WU-3 / E1.6 entry 8: two-REAL-child interleave proof for the transport-repetition sidecar.

The transport-repetition sidecar
(``ralph.mcp.server._transport_repetition_tracker``) is a parent-owned
JSON file carried across child process boundaries. Every MCP subprocess
opens the SAME path (resolved from ``MCP_SNAPSHOT_DIR_ENV``), reads the
previous child's streak with the lock held, advances the streak locally,
and writes the merged result back. Concurrency safety rests on
``fcntl.flock`` in ``_acquire_state_lock`` (``_transport_repetition_tracker.py:252``)
plus a tmp-then-rename write in ``_write_carried_state`` so a peer
observer never reads a partial payload.

A regression that drops the lock, breaks the rename, or accidentally
splits the read and write outside the lock section would let two
concurrent observes overwrite each other and lose observations. This
file drives that hazard under real subprocess contention so the failure
class is observable here, not silently accumulating in production.

The marker below (``subprocess_e2e``) is non-negotiable: this file
crosses a process boundary via real ``multiprocessing.Process(`` fork
spins, so the audit identifies it as a real-subprocess test and the
default suite excludes it under ``-m 'not subprocess_e2e'``. Marking it
without the marker would have the audit flag the
``multiprocessing.Process(...)`` and ``Path.open(...)`` calls as policy
violations.
"""

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
) -> None:  # pragma: no cover - exercised in subprocess
    """Real subprocess child: observe ``signature`` ``count`` times.

    The child gets the snapshot directory through the parent-owned
    environment so it resolves ``repetition_state_path`` to the SAME
    file the parent test driver will read. A non-zero exit code
    surfaces any observation failure (e.g. ``OSError`` in the
    ``_write_carried_state`` path).
    """
    import os

    # The parent passes the snapshot directory through the environment so
    # both children resolve ``repetition_state_path`` to the SAME file —
    # that is the surface the lock guards.
    os.environ["RALPH_MCP_SNAPSHOT_DIR"] = str(snapshot_dir)
    # Per-spawn re-init so each child starts with a fresh module-level
    # spawn id (the tracker itself doesn't key on the spawn id, only on
    # the ``state_path`` passed through the env).
    tracker = TransportRepetitionTracker.from_parent_owned_state()
    if tracker.state_path is None:
        raise RuntimeError("TransportRepetitionTracker.state_path must be set in subprocess")
    for _ in range(count):
        tracker.observe(signature)


def _read_carried_streak(state_path: Path) -> int:
    """Return the streak stored on disk, or 0 when the file is absent.

    Mirrors the production reader
    (``_read_carried_state``) without depending on its private name. A
    missing file is treated as ``0`` — the same semantics the tracker
    uses for an absent carried record.
    """
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
    """Drive two forked children and return ``(carried_streak, observations_made)``.

    ``carried_streak`` is the streak written by the last child. The
    expected value under a properly-serialised protocol is
    ``observations_made`` (each observe increments by one), because all
    observers here use the SAME signature within ``window_seconds``.
    A regression that lets two concurrent observes clobber each other
    drops the carried streak below ``observations_made``.

    Returns the carry-streak so the caller can assert both children
    landed their observations AND the on-disk record is correct.
    """
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
    """Two real children interleaving observe() calls preserve every observation.

    Drives two ``multiprocessing.Process`` fork children that each call
    ``TransportRepetitionTracker.observe(SAME_SIGNATURE)`` ``count_per_child`` times
    against the SAME parent-owned state path. The carried streak on
    disk must equal the total number of observations: a properly-locked
    read-modify-write preserves every observe; an unlocked RMW drops
    some because two processes can both read the old streak, both
    increment, and the second writer's value overwrites the first's.
    """
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
    """Carried streak from one child is observable + extendable by a second.

    This is the production make-before-break pattern: the OLD child
    observes some failures and lands the streak on disk; the NEW child
    inherits that streak and continues. Two forked processes run in
    sequence here (``fork`` with one child at a time is still a real
    subprocess, satisfying the audit) and the second child must end
    with a streak of ``first_obs + second_obs`` — not just ``second_obs``.
    A regression that breaks the read under the lock (i.e. two writers
    that only see their own work) fails this test.
    """
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
