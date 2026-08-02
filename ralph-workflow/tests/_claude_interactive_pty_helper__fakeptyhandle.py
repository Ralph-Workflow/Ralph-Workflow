from __future__ import annotations


class _FakePtyHandle:
    def __init__(self) -> None:
        self.record = type("Record", (), {"pid": 321, "status": None})()
        self.returncode = 0
        self.master_fd = 77

    def __enter__(self) -> _FakePtyHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0
