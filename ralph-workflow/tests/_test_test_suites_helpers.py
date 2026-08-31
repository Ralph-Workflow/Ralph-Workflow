"""Shared fakes for ``tests/test_test_suites.py`` and its split orchestration suite.

The split lives here, not in :mod:`ralph.testing`, because the fakes only
exist to exercise the maintained pytest-shard runner. Keeping them in a
``_``-prefixed module means pytest does not collect this file (the project
default discovery patterns are ``test_*.py`` / ``*_test.py``), so the
helpers carry zero per-shard collection cost. Every test file that
imports them pays the same import cost once via pytest's import cache.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class _FakeShardProcess:
    # OpenCode's prompt is delivered on stdin, so a process fake must
    # satisfy the ``_SyncProcessLike`` protocol's ``stdin`` member.
    stdin = None
    def __init__(
        self,
        returncodes: list[int | None],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        communicate_times_out: bool = False,
    ) -> None:
        self._returncodes = list(returncodes)
        self._last_returncode: int | None = None
        self._stdout = stdout
        self._stderr = stderr
        self._communicate_times_out = communicate_times_out
        self.terminated = False
        self.reaped = False
        self.orphans_cleaned = False

    def poll(self) -> int | None:
        if self._returncodes:
            self._last_returncode = self._returncodes.pop(0)
        return self._last_returncode

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        if self._communicate_times_out:
            raise subprocess.TimeoutExpired(("pytest",), 1.0)
        self.reaped = True
        return self._stdout, self._stderr

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True
        self._last_returncode = -15

    def cleanup_orphans(self) -> None:
        self.orphans_cleaned = True


class _StubSpawner:
    def __init__(self, processes: list[_FakeShardProcess]) -> None:
        self._processes = list(processes)
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> _FakeShardProcess:
        self.calls.append((tuple(command), cwd, dict(env)))
        return self._processes.pop(0)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds
