from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

from tests.test_agents_invoke_5_helper__blockingstdout import _BlockingStdout


class _FakeInvokeProcess:
    """Minimal subprocess.Popen stand-in for integration tests."""

    pid: int = 77777

    def __init__(self, stdout: object = None) -> None:
        self.stdout = stdout or _BlockingStdout()
        self.stderr = SimpleNamespace(read=lambda: "")
        self.returncode: int | None = None

    def __enter__(self) -> _FakeInvokeProcess:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> Literal[False]:
        return False

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def poll(self) -> int | None:
        return self.returncode
