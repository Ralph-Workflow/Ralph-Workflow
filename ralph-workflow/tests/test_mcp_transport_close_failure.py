"""Regression tests for stdio transport close failure handling."""

from __future__ import annotations

import contextlib
import itertools
import sys

import pytest

import ralph.process.manager as _mgr
from ralph.mcp.protocol.transport import StdioTransport
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessTerminationError,
    get_process_manager,
    reset_process_manager,
)
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing.fake_process import FakeImmortalPopen, make_async_process_factory

PYTHON = sys.executable

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
)


@pytest.mark.asyncio
async def test_stdio_transport_close_surfaces_managed_process_termination_failure() -> None:
    """Transport close must not silently report success when terminate() fails."""
    reset_process_manager()

    def immortal_factory(command: object, opts: object) -> FakeImmortalPopen:
        del command, opts
        return FakeImmortalPopen(pid=1)

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=immortal_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=None,
    )
    original = _mgr._pm_state.instance
    _mgr._pm_state.instance = pm
    try:
        transport = StdioTransport([PYTHON, "-c", "pass"])
        transport.start()

        with pytest.raises(ProcessTerminationError):
            await transport.close()

        records = pm.list_records(
            include_active=False,
            include_terminal=True,
            label_prefix="mcp-stdio:",
        )
        assert len(records) == 1
        assert records[0].status == ProcessStatus.FAILED
        assert records[0].cause == "termination_failed"
        assert records[0].failure_message == "Process still alive after kill"
    finally:
        _mgr._pm_state.instance = original
        with contextlib.suppress(Exception):
            get_process_manager().shutdown_all(grace_period_s=0)
        reset_process_manager()
