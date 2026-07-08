"""Regression tests for ``ProcessManager`` async-termination executor ownership.

wt-024 memory-perf AC-02: ``ProcessManager._escalate_termination_async``
historically called ``loop.run_in_executor(None, _do_terminate)``
(line ~1273 of ``_process_manager.py``), borrowing the asyncio loop's
default ``ThreadPoolExecutor``. The default executor is NOT owned by
``ProcessManager`` and is NOT released on ``shutdown_all(...)``. In
Python 3.14, ``concurrent.futures.ThreadPoolExecutor`` workers are
non-daemon by default and are joined by ``concurrent.futures``' own
``_python_exit`` atexit handler — a stuck default-executor termination
worker can block process exit on interpreter shutdown, holding the
process alive past the watchdog's deadline.

The fix introduces a DEDICATED bounded ``ThreadPoolExecutor`` owned
by ``ProcessManager`` and exposes it through an injectable seam
(``_get_terminate_executor``). ``_escalate_termination_async`` passes
that executor to ``loop.run_in_executor(...)``; ``shutdown_all``'s
finally block calls ``self._terminate_executor.shutdown(wait=False)``
and nulls the field. Because ``_do_terminate`` uses ONLY bounded
``psutil.wait_procs(timeout=...)`` calls, any in-flight termination
worker completes within ``grace_period_s + policy.kill_followup_timeout_s``
and is reaped by ``concurrent.futures``' own atexit join — no
orphaned termination worker blocks process exit.

These tests pin both halves of that contract:

1. The executor passed to ``loop.run_in_executor`` is the
   ProcessManager-owned one (NOT ``None``), and it has bounded
   ``max_workers``.
2. After ``shutdown_all(...)``, that executor's ``shutdown`` was
   called (releasing the dedicated executor on process exit).

Both assertions must FAIL on the current un-fixed code (which passes
``None`` and has no ownership / release path).
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, cast

import pytest

from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import (
    FakePsutil,
    make_async_process_factory,
    make_sync_process_factory,
)

if TYPE_CHECKING:
    from collections.abc import Callable


_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)


class _RecordingExecutor:
    """Fake ``concurrent.futures.Executor`` that records its submit/shutdown calls.

    Used by tests that want to inject a known executor through the
    ``_get_terminate_executor`` seam and observe ``shutdown(...)``
    directly. Mirrors the production ``ThreadPoolExecutor`` shape
    closely enough that ``loop.run_in_executor`` accepts it (the
    ``AbstractEventLoop.run_in_executor`` calls ``executor.submit``).
    """

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
    """AC-02 canonical regression: ``_escalate_termination_async`` must pass a
    DEDICATED bounded executor to ``loop.run_in_executor`` (NOT ``None``),
    and that executor must be the ProcessManager-owned one.
    """
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=async_factory,
        psutil=FakePsutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    assert handle.record.status.name == "RUNNING"

    # Spy on the event-loop's run_in_executor to capture the executor
    # argument. The bug is that the current code passes None here;
    # the fix passes the ProcessManager-owned executor.
    captured: dict[str, object | None] = {"executor": None, "call_count": 0}
    loop = asyncio.get_running_loop()
    original_run_in_executor = loop.run_in_executor

    def _spy_run_in_executor(executor: object, *args: object, **kwargs: object) -> object:
        captured["executor"] = executor
        captured["call_count"] = cast("int", captured["call_count"]) + 1
        # Forward to the original bound method using positional args
        # only — run_in_executor's signature is (self, executor, func, *args)
        # on the unbound form. As a bound method it is (executor, func, *args).
        return original_run_in_executor(executor, *args, **kwargs)

    monkeypatch.setattr(loop, "run_in_executor", _spy_run_in_executor)

    await handle.terminate(grace_period_s=0.01)

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
    # The dedicated executor MUST be bounded. ThreadPoolExecutor
    # exposes its cap as ``_max_workers``; the test asserts the
    # fixed code uses a ThreadPoolExecutor (or compatible) with a
    # small positive cap, NOT the unbounded default executor.
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
    """AC-02 second half: ``shutdown_all`` MUST release the dedicated executor.

    Verifies the second leg of the contract: after ``shutdown_all``,
    the ProcessManager-owned termination executor's ``shutdown()``
    was called. This is what prevents the default-executor leak the
    bug introduced: the default executor is never released by
    ProcessManager, the dedicated executor IS.

    The test injects a recording executor through the
    ``_get_terminate_executor`` seam so we can observe ``shutdown``
    directly without spying on the event loop.
    """
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=async_factory,
        psutil=FakePsutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    assert handle.record.status.name == "RUNNING"

    # Inject a recording executor through the seam so we can
    # observe ``shutdown`` directly. The seam is the canonical
    # injection point per AC-02 design.
    recording = _RecordingExecutor(max_workers=4)
    monkeypatch.setattr(pm, "_get_terminate_executor", lambda: recording)

    await handle.terminate(grace_period_s=0.01)

    # Mirror what the production ``_get_terminate_executor`` seam does
    # on its lazy-allocate branch: populate the field so the
    # ``shutdown_all`` release-path guard ``if self._terminate_executor
    # is not None`` fires on this executor (the production code would
    # have set this field on first seam call).
    pm._terminate_executor = recording

    # Sanity: no shutdown yet (the terminate path itself doesn't release).
    assert recording.shutdown_calls == [], (
        f"the dedicated executor MUST NOT be released by terminate; "
        f"shutdown_all is the release point. got {recording.shutdown_calls!r}"
    )

    # Now call shutdown_all — this is the canonical release point.
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
    """AC-02 lazy-allocation regression: ``shutdown_all`` MUST NOT allocate the
    dedicated terminate executor when no async termination was ever performed.

    The dedicated ``ThreadPoolExecutor`` is created lazily on first
    async-termination use so managers that only ever spawn sync / PTY
    processes never allocate the worker pool (and never spin up its
    threads). A previous version of ``shutdown_all`` invoked
    ``self._get_terminate_executor()`` unconditionally in its finally
    block, which allocated a fresh ``ThreadPoolExecutor`` on every
    fresh manager and then immediately shut it down — defeating the
    lazy-allocation contract.

    This test instantiates a fresh ``ProcessManager`` (no
    ``spawn_async``, no async termination), spies on the
    ``_get_terminate_executor`` seam, calls ``shutdown_all``, and
    asserts the seam was NEVER touched and ``self._terminate_executor``
    remains ``None`` throughout.
    """
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=make_async_process_factory(itertools.count(1)),
        psutil=FakePsutil(),
    )

    assert pm._terminate_executor is None, (
        "fresh ProcessManager MUST start with _terminate_executor=None "
        "(lazy allocation; no async work has been done)"
    )

    # Spy on the seam: record call count AND fail loudly if it is
    # ever called. The seam is the canonical injection point used by
    # AC-02's tests; counting its invocations proves whether the
    # release path lazily allocates an executor or guards on the
    # existing field.
    seam_calls: list[object] = []

    def _spy_seam() -> ThreadPoolExecutor:
        seam_calls.append(object())
        # Forward to the real seam so the rest of the contract still
        # works if the field IS already populated (it should not be
        # in this test, but keep behaviour observable).
        return (
            pm._terminate_executor
            if pm._terminate_executor is not None
            else (
                ThreadPoolExecutor(
                    max_workers=ProcessManager._TERMINATE_EXECUTOR_MAX_WORKERS,
                    thread_name_prefix="ralph-terminate",
                )
            )
        )

    monkeypatch.setattr(pm, "_get_terminate_executor", _spy_seam)

    pm.shutdown_all(grace_period_s=0.01)

    assert seam_calls == [], (
        f"shutdown_all MUST NOT call ``_get_terminate_executor`` on a "
        f"manager that never performed async termination. Lazy "
        f"allocation is the contract: a fresh manager that never "
        f"spawned an async process must not create the dedicated "
        f"ThreadPoolExecutor, even during teardown. Got {len(seam_calls)} "
        f"unwanted seam call(s)."
    )
    assert pm._terminate_executor is None, (
        f"after shutdown_all on an unused manager, _terminate_executor "
        f"MUST remain None (lazy-allocation contract). Got {pm._terminate_executor!r}"
    )
