from __future__ import annotations


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        pass

    def set_status(self, unit_id: str, status: object) -> None:
        pass

    def __enter__(self) -> _FakeDisplay:
        return self

    def __exit__(self, *args: object) -> None:
        pass
