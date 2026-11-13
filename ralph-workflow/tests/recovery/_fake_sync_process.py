from __future__ import annotations

FAKE_PROCESS_PID = 424242


class FakeSyncProcess:
    def __init__(self, returncode: int | None) -> None:
        self.pid = FAKE_PROCESS_PID
        self.returncode = returncode
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes | None, bytes | None]:
        return None, None

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None
