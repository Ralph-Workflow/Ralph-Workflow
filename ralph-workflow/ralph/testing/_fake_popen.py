from __future__ import annotations

import subprocess
from typing import IO, cast

from ralph.testing._process_state import ProcessState
from ralph.testing._process_streams import ProcessStreams


class FakePopen:
    """Minimal subprocess.Popen-like fake for testing."""

    def __init__(
        self,
        pid: int,
        *,
        state: ProcessState | None = None,
        streams: ProcessStreams | None = None,
    ) -> None:
        self.pid = pid
        state = state or ProcessState()
        streams = streams or ProcessStreams()
        self._returncode = state.returncode
        self._terminated = state.terminated
        self._killed = state.killed
        self.stdin: IO[bytes] | None = streams.stdin
        self.stdout: IO[bytes] | None = streams.stdout
        self.stderr: IO[bytes] | None = streams.stderr

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode if self._returncode is not None else 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]:
        return None, None

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._killed = True


def _stubborn_init(self: object, pid: int, *, final_returncode: int = -9) -> None:
    object.__setattr__(self, 'pid', pid)
    object.__setattr__(self, '_returncode', None)
    object.__setattr__(self, '_final_returncode', final_returncode)
    object.__setattr__(self, '_killed', False)
    object.__setattr__(self, 'stdin', None)
    object.__setattr__(self, 'stdout', None)
    object.__setattr__(self, 'stderr', None)


def _stubborn_returncode(self: object) -> int | None:
    return cast('int | None', object.__getattribute__(self, '_returncode'))


def _stubborn_poll(self: object) -> int | None:
    return cast('int | None', object.__getattribute__(self, '_returncode'))


def _stubborn_wait(self: object, timeout: float | None = None) -> int:
    if cast('bool', object.__getattribute__(self, '_killed')):
        final_returncode = cast('int', object.__getattribute__(self, '_final_returncode'))
        object.__setattr__(self, '_returncode', final_returncode)
        return final_returncode
    raise subprocess.TimeoutExpired(cmd='fake-stubborn', timeout=timeout or 0.0)


def _stubborn_communicate(
    self: object, input: bytes | None = None, timeout: float | None = None
) -> tuple[bytes | None, bytes | None]:
    del self, input, timeout
    return None, None


def _stubborn_terminate(self: object) -> None:
    del self


def _stubborn_kill(self: object) -> None:
    object.__setattr__(self, '_killed', True)


def _immortal_init(self: object, pid: int) -> None:
    object.__setattr__(self, 'pid', pid)
    object.__setattr__(self, '_returncode', None)
    object.__setattr__(self, 'stdin', None)
    object.__setattr__(self, 'stdout', None)
    object.__setattr__(self, 'stderr', None)


def _immortal_returncode(self: object) -> int | None:
    return cast('int | None', object.__getattribute__(self, '_returncode'))


def _immortal_poll(self: object) -> int | None:
    return cast('int | None', object.__getattribute__(self, '_returncode'))


def _immortal_wait(self: object, timeout: float | None = None) -> int:
    raise subprocess.TimeoutExpired(cmd='fake-immortal', timeout=timeout or 0.0)


def _immortal_communicate(
    self: object, input: bytes | None = None, timeout: float | None = None
) -> tuple[bytes | None, bytes | None]:
    del self, input, timeout
    return None, None


def _immortal_terminate(self: object) -> None:
    del self


def _immortal_kill(self: object) -> None:
    del self


_STUBBORN_NAMESPACE: dict[str, object] = {
    '__doc__': 'FakePopen that ignores SIGTERM but obeys SIGKILL.',
    '__init__': _stubborn_init,
    'returncode': property(_stubborn_returncode),
    'poll': _stubborn_poll,
    'wait': _stubborn_wait,
    'communicate': _stubborn_communicate,
    'terminate': _stubborn_terminate,
    'kill': _stubborn_kill,
}

_IMMORTAL_NAMESPACE: dict[str, object] = {
    '__doc__': 'FakePopen that never terminates regardless of signal.',
    '__init__': _immortal_init,
    'returncode': property(_immortal_returncode),
    'poll': _immortal_poll,
    'wait': _immortal_wait,
    'communicate': _immortal_communicate,
    'terminate': _immortal_terminate,
    'kill': _immortal_kill,
}

FakeStubbornPopen = type('FakeStubbornPopen', (), _STUBBORN_NAMESPACE)
FakeImmortalPopen = type('FakeImmortalPopen', (), _IMMORTAL_NAMESPACE)


__all__ = ['FakeImmortalPopen', 'FakePopen', 'FakeStubbornPopen']
