from __future__ import annotations

from typing import TYPE_CHECKING, Any

_sync_cell: list[_SyncProcessFactory] = []
_async_cell: list[_AsyncProcessFactory] = []
_pty_cell: list[_PtyProcessFactory] = []


def _set_defaults(
    sync: _SyncProcessFactory,
    async_: _AsyncProcessFactory,
    pty: _PtyProcessFactory,
) -> None:
    _sync_cell[:] = [sync]
    _async_cell[:] = [async_]
    _pty_cell[:] = [pty]


if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Iterable, Sequence
    from typing import IO, Protocol

    from ralph.process.manager._spawn_options import SpawnOptions

    class _PsutilProcessLike(Protocol):
        pid: int

        @property
        def info(self) -> dict[str, int]: ...

        def children(self, recursive: bool = False) -> Sequence[_PsutilProcessLike]: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...
        def is_running(self) -> bool: ...
        def status(self) -> str: ...
        def create_time(self) -> float: ...

    class _PsutilModuleLike(Protocol):
        NoSuchProcess: type[BaseException]
        AccessDenied: type[BaseException]
        Process: Callable[[int], _PsutilProcessLike]

        def process_from_pid(self, pid: int) -> _PsutilProcessLike: ...

        def process_iter(
            self, attrs: Sequence[str] | None = None
        ) -> Iterable[_PsutilProcessLike]: ...

        def wait_procs(
            self,
            procs: Sequence[_PsutilProcessLike],
            timeout: float | None = None,
        ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]: ...

    class _SyncProcessLike(Protocol):
        pid: int

        @property
        def stdin(self) -> IO[bytes] | None: ...

        @property
        def stdout(self) -> IO[bytes] | None: ...

        @property
        def stderr(self) -> IO[bytes] | None: ...

        @property
        def returncode(self) -> int | None: ...

        def poll(self) -> int | None: ...
        def wait(self, timeout: float | None = None) -> int: ...
        def communicate(
            self,
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes | None, bytes | None]: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...

    class _AsyncProcessLike(Protocol):
        pid: int

        @property
        def stdin(self) -> asyncio.StreamWriter | None: ...

        @property
        def stdout(self) -> asyncio.StreamReader | None: ...

        @property
        def stderr(self) -> asyncio.StreamReader | None: ...

        @property
        def returncode(self) -> int | None: ...

        async def wait(self) -> int: ...
        async def communicate(
            self, input: bytes | None = None
        ) -> tuple[bytes | None, bytes | None]: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...

    class _SyncProcessFactory(Protocol):
        def __call__(
            self,
            command: Sequence[str],
            opts: SpawnOptions,
        ) -> _SyncProcessLike: ...

    class _AsyncProcessFactory(Protocol):
        async def __call__(
            self,
            command: Sequence[str],
            *,
            cwd: str | None,
            env: dict[str, str] | None,
            stdin: int | None,
            stdout: int | None,
            stderr: int | None,
            start_new_session: bool,
        ) -> _AsyncProcessLike: ...

    class _PtyProcessLike(Protocol):
        pid: int
        master_fd: int
        slave_fd: int

        @property
        def returncode(self) -> int | None: ...

        def poll(self) -> int | None: ...
        def wait(self, timeout: float | None = None) -> int: ...
        def terminate(self) -> None: ...
        def kill(self) -> None: ...
        def close(self) -> None: ...
        def fileno(self) -> int: ...
        def isatty(self) -> bool: ...

    class _PtyProcessFactory(Protocol):
        def __call__(
            self,
            command: Sequence[str],
            *,
            cwd: str | None,
            env: dict[str, str] | None,
            cols: int,
            rows: int,
        ) -> _PtyProcessLike: ...
else:
    _PsutilProcessLike = Any
    _PsutilModuleLike = Any
    _SyncProcessLike = Any
    _AsyncProcessLike = Any
    _SyncProcessFactory = Any
    _AsyncProcessFactory = Any
    _PtyProcessLike = Any
    _PtyProcessFactory = Any
    Callable = Any


__all__ = [
    "_AsyncProcessFactory",
    "_AsyncProcessLike",
    "_PsutilModuleLike",
    "_PsutilProcessLike",
    "_PtyProcessFactory",
    "_PtyProcessLike",
    "_SyncProcessFactory",
    "_SyncProcessLike",
    "_async_cell",
    "_pty_cell",
    "_set_defaults",
    "_sync_cell",
]
