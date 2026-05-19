from __future__ import annotations


class _DummyRichProgress:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    def __enter__(self) -> _DummyRichProgress:
        self.entered = True
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool | None:
        self.exited = True
        return None
