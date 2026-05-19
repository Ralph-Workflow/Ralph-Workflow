from __future__ import annotations


class _PreloadedStdout:
    """Stdout that yields pre-loaded lines and then closes."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def __iter__(self) -> _PreloadedStdout:
        return self

    def __next__(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        raise StopIteration
