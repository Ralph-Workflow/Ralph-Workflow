from __future__ import annotations


class _ExplodingResponse:
    def __init__(self, detail: str) -> None:
        self.detail = detail

    def raise_for_status(self) -> None:
        raise RuntimeError(self.detail)
