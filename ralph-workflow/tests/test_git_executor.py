"""Tests for GitExecutor serialized gate."""

import asyncio
import threading

import pytest

from ralph.git.executor import GitExecutor

_SIMPLE_RESULT = 42
_THREAD_COUNT = 8
_OPS_PER_THREAD = 100
_TOTAL_OPS = _THREAD_COUNT * _OPS_PER_THREAD
_GATHER_COUNT = 3


def test_run_executes_callable() -> None:
    ge = GitExecutor()
    result = ge.run(lambda: _SIMPLE_RESULT)
    assert result == _SIMPLE_RESULT


def test_run_propagates_exceptions() -> None:
    ge = GitExecutor()
    with pytest.raises(ValueError, match="test error"):
        ge.run(lambda: (_ for _ in ()).throw(ValueError("test error")))


def test_concurrent_ops_serialize() -> None:
    """8 threads x 100 ops; execution order recorded; no contention errors."""
    ge = GitExecutor()
    results: list[int] = []
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        for i in range(_OPS_PER_THREAD):
            try:
                val = ge.run(lambda _i=i: idx * _OPS_PER_THREAD + _i)
                with lock:
                    results.append(val)
            except Exception as e:
                with lock:
                    errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(_THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == _TOTAL_OPS


@pytest.mark.asyncio
async def test_arun_from_coroutine() -> None:
    ge = GitExecutor()
    result = await ge.arun(lambda: "async result")
    assert result == "async result"


@pytest.mark.asyncio
async def test_arun_concurrent_serializes() -> None:
    ge = GitExecutor()
    running_count = 0
    max_concurrent = 0
    lock = threading.Lock()
    op_release = threading.Event()
    op_release.set()

    def slow_op() -> int:
        nonlocal running_count, max_concurrent
        with lock:
            running_count += 1
            max_concurrent = max(max_concurrent, running_count)
        op_release.wait()
        with lock:
            running_count -= 1
        return 1

    results = await asyncio.gather(
        ge.arun(slow_op),
        ge.arun(slow_op),
        ge.arun(slow_op),
    )
    assert sum(results) == _GATHER_COUNT
    assert max_concurrent == 1
