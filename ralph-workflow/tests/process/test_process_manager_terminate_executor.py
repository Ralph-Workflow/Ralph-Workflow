from __future__ import annotations

import asyncio
import itertools
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from ralph.process.manager import ProcessManager, ProcessManagerPolicy, ProcessTerminationIssuer
from ralph.testing.fake_process import (
    FakePsutil,
    make_async_process_factory,
    make_sync_process_factory,
)
from tests._support.typed_accessors import must_int

if TYPE_CHECKING:
    from collections.abc import Callable


_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)


class _RecordingExecutor:
    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max_workers
        self.shutdown_calls: list[bool] = []
        self.submit_calls = 0
        self._delegate = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, fn: Callable[..., object], *args: object, **kwargs: object) -> object:
        self.submit_calls += 1
        return self._delegate.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append(wait)
        self._delegate.shutdown(wait=wait, cancel_futures=cancel_futures)


@pytest.mark.timeout_seconds(5)
@pytest.mark.asyncio
async def test_async_termination_uses_bounded_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=async_factory,
        psutil=FakePsutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    assert handle.record.status.name == "RUNNING"

    captured: dict[str, object | None] = {"executor": None, "call_count": 0}
    loop = asyncio.get_running_loop()
    original_run_in_executor = loop.run_in_executor

    def _spy_run_in_executor(executor: object, *args: object, **kwargs: object) -> object:
        captured["executor"] = executor
        captured["call_count"] = must_int(captured["call_count"]) + 1
        return original_run_in_executor(executor, *args, **kwargs)

    monkeypatch.setattr(loop, "run_in_executor", _spy_run_in_executor)

    await handle.terminate(grace_period_s=0.01, issuer=ProcessTerminationIssuer.OPERATOR)

    assert captured["call_count"] >= 1, (
        "_escalate_termination_async must call loop.run_in_executor at least once"
    )
    assert captured["executor"] is not None, (
        "_escalate_termination_async MUST pass a ProcessManager-owned "
        "executor to loop.run_in_executor (NOT None). Passing None "
        "borrows the asyncio loop's default executor, which is never "
        "released on shutdown_all and can block process exit if a "
        "termination worker is stuck."
    )
    executor = captured["executor"]
    max_workers = getattr(executor, "_max_workers", None) or getattr(executor, "max_workers", None)
    assert max_workers is not None, (
        "the dedicated executor MUST expose its max_workers cap "
        "(ThreadPoolExecutor exposes it as _max_workers)"
    )
    assert 1 <= max_workers <= 64, (
        f"the dedicated terminate executor MUST have a small bounded "
        f"max_workers (1..64); got {max_workers!r}. An unbounded "
        f"executor is the leak the contract prevents."
    )


@pytest.mark.timeout_seconds(5)
@pytest.mark.asyncio
async def test_shutdown_all_releases_terminate_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    recording = _RecordingExecutor(max_workers=4)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=async_factory,
        psutil=FakePsutil(),
        terminate_executor_factory=lambda: recording,
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    assert handle.record.status.name == "RUNNING"

    await handle.terminate(grace_period_s=0.01, issuer=ProcessTerminationIssuer.OPERATOR)

    assert pm.terminate_executor is recording

    assert recording.shutdown_calls == [], (
        f"the dedicated executor MUST NOT be released by terminate; "
        f"shutdown_all is the release point. got {recording.shutdown_calls!r}"
    )

    pm.shutdown_all(grace_period_s=0.01)

    assert recording.shutdown_calls == [False], (
        f"shutdown_all MUST release the dedicated terminate executor via "
        f"shutdown(wait=False) (does NOT block on in-flight workers). "
        f"Got shutdown calls: {recording.shutdown_calls!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_shutdown_all_does_not_allocate_terminate_executor_on_unused_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seam_calls: list[object] = []

    def _spy_seam() -> ThreadPoolExecutor:
        seam_calls.append(object())
        return ThreadPoolExecutor(
            max_workers=ProcessManager.TERMINATE_EXECUTOR_MAX_WORKERS,
            thread_name_prefix="ralph-terminate",
        )

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=make_async_process_factory(itertools.count(1)),
        psutil=FakePsutil(),
        terminate_executor_factory=_spy_seam,
    )

    assert pm.terminate_executor is None, (
        "fresh ProcessManager MUST start with no terminate executor "
        "(lazy allocation; no async work has been done)"
    )

    pm.shutdown_all(grace_period_s=0.01)

    assert seam_calls == [], (
        f"shutdown_all MUST NOT call ``_get_terminate_executor`` on a "
        f"manager that never performed async termination. Lazy "
        f"allocation is the contract: a fresh manager that never "
        f"spawned an async process must not create the dedicated "
        f"ThreadPoolExecutor, even during teardown. Got {len(seam_calls)} "
        f"unwanted seam call(s)."
    )
    assert pm.terminate_executor is None, (
        f"after shutdown_all on an unused manager, the terminate executor "
        f"MUST remain unallocated (lazy-allocation contract). Got {pm.terminate_executor!r}"
    )
