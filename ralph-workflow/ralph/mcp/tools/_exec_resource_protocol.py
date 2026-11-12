from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class ExecResourceEntryLike(Protocol):
    def resource_list_entry(self) -> dict[str, object]: ...


class ExecResourceResolverLike(Protocol):
    def read(self, uri: str) -> tuple[bytes, str, int] | None: ...

    def list_entries(self) -> Iterable[ExecResourceEntryLike]: ...

    def register(self, spill_path: Path) -> str: ...


__all__ = [
    "ExecResourceEntryLike",
    "ExecResourceResolverLike",
]
