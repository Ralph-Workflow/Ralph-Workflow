"""Regression tests for the fs-health ProcessManager seam."""

from __future__ import annotations

import itertools

from ralph.diagnostics import fs_health
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import (
    FakePsutil,
    make_async_process_factory,
    make_sync_process_factory,
)


def test_fs_health_regression_mdutil_is_tracked_by_process_manager() -> None:
    """S-9: mdutil must register and complete through the injected manager."""
    process_manager = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False),
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=make_async_process_factory(itertools.count(1)),
        psutil=FakePsutil(),
    )

    result = fs_health._run_subprocess_mdutil(
        ["mdutil", "-s", "/Volumes/test"],
        capture_output=True,
        text=True,
        timeout=10,
        process_manager=process_manager,
    )

    assert result.returncode == -1
    records = process_manager.list_records(include_active=True, include_terminal=True)
    assert len(records) == 1
    assert records[0].label == "diagnostics:mdutil"
    assert records[0].status.name in {"EXITED", "KILLED"}
