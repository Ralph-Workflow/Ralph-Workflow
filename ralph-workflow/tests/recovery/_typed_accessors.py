from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeGuard, runtime_checkable

from ralph.checked_accessors import as_mapping
from ralph.process.teardown import ProcessTeardown

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess

__all__ = [
    "is_managed_process",
    "is_managed_pty_process",
    "json_object",
    "managed_process",
    "managed_pty_process",
    "process_teardown",
]


@runtime_checkable
class _ManagedProcessShape(Protocol):
    stdin: object
    stdout: object
    stderr: object
    pid: object
    returncode: object

    def poll(self) -> int | None: ...

    def terminate(self, grace_period_s: float | None = None) -> None: ...


@runtime_checkable
class _ManagedPtyProcessShape(Protocol):
    master_fd: object
    pid: object
    returncode: object

    def poll(self) -> int | None: ...

    def terminate(self, grace_period_s: float | None = None) -> None: ...


def json_object(value: object, *, field: str) -> dict[str, object]:
    return dict(as_mapping(value, field=field))


def is_managed_process(value: object) -> TypeGuard[ManagedProcess]:
    return isinstance(value, _ManagedProcessShape)


def is_managed_pty_process(value: object) -> TypeGuard[ManagedPtyProcess]:
    return isinstance(value, _ManagedPtyProcessShape)


def managed_process(value: object) -> ManagedProcess:
    if not is_managed_process(value):
        raise TypeError(f"expected a managed process handle, got {type(value).__name__}")
    return value


def managed_pty_process(value: object) -> ManagedPtyProcess:
    if not is_managed_pty_process(value):
        raise TypeError(f"expected a managed pty process handle, got {type(value).__name__}")
    return value


def process_teardown(value: object) -> ProcessTeardown:
    if not isinstance(value, ProcessTeardown):
        raise TypeError(f"expected a process teardown, got {type(value).__name__}")
    return value
