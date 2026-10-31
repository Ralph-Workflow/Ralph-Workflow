"""Regression: ``cleanup_orphans`` must never signal Ralph's own process group.

``ProcessManager``'s group sweep (``os.killpg(pgid, SIGKILL)``) only refused
PGIDs 0 and 1. A record can carry Ralph's OWN group -- a PTY child that never
became a session leader, or a caller handing a raw PGID to
``cleanup_orphans`` -- and ``killpg`` on it SIGKILLs Ralph itself plus every
sibling in its terminal group. ``ralph.process.teardown`` already refuses that
group; the manager's sweep must refuse it too.
"""

from __future__ import annotations

import os
import sys

import pytest

from ralph.process.manager import ProcessManager

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "killpg"),
    reason="POSIX process groups only",
)


def _record_killpg(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []

    def _fake_killpg(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))

    monkeypatch.setattr(os, "killpg", _fake_killpg)
    return calls


def test_cleanup_orphans_refuses_own_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing Ralph's own PGID is a no-op, never a group kill."""
    calls = _record_killpg(monkeypatch)
    pm = ProcessManager(psutil=None)

    pm.cleanup_orphans(os.getpgid(0))

    assert calls == []


def test_cleanup_orphans_still_kills_a_foreign_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is narrow: any other valid PGID is still swept."""
    calls = _record_killpg(monkeypatch)
    pm = ProcessManager(psutil=None)
    foreign_pgid = os.getpgid(0) + 100_000

    pm.cleanup_orphans(foreign_pgid)

    assert foreign_pgid in [pgid for pgid, _sig in calls]
