"""Context manager that suppresses OSError on file descriptor close."""

from __future__ import annotations


class _SuppressCloseError:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        del exc, tb
        return exc_type is OSError
