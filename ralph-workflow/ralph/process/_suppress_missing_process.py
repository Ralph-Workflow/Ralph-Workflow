"""Context manager that suppresses ProcessLookupError and PermissionError."""

from __future__ import annotations


class _SuppressMissingProcess:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        del exc, tb
        return exc_type in (ProcessLookupError, PermissionError)
