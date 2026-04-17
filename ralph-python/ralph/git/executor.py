"""GitExecutor — serialized gate for GitPython operations.

GitPython is not thread-safe when multiple threads share a single .git/ directory.
GitExecutor wraps all repo-mutating operations in a single-threaded executor,
ensuring only one git operation runs at a time regardless of how many asyncio
tasks or threads call it concurrently.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


class GitExecutor:
    """Serialized gate for GitPython operations.

    All calls to `run()` are executed sequentially in a single background
    thread (max_workers=1), preventing concurrent GitPython access.

    Attributes:
        _executor: Single-threaded executor that serializes all git ops.
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1)

    def run(self, op: Callable[[], T]) -> T:
        """Run a git operation synchronously, serialized with all other ops.

        Args:
            op: Zero-argument callable returning T.

        Returns:
            Result of op().

        Raises:
            Any exception raised by op().
        """
        future = self._executor.submit(op)
        return future.result()

    async def arun(self, op: Callable[[], T]) -> T:
        """Run a git operation asynchronously without blocking the event loop.

        Uses loop.run_in_executor which runs op in the thread pool. Combined
        with max_workers=1, this ensures serialization even from coroutines.

        Args:
            op: Zero-argument callable returning T.

        Returns:
            Awaitable result of op().
        """
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, op)
        return await future

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
