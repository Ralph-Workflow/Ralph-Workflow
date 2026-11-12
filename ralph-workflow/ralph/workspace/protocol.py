from __future__ import annotations

from typing import Protocol, runtime_checkable

from ralph.workspace._snapshot import WorkspaceSnapshot


@runtime_checkable
class Workspace(Protocol):
    def read(self, path: str) -> str: ...

    def snapshot(self, path: str, *, max_bytes: int | None = None) -> WorkspaceSnapshot: ...

    def write(self, path: str, content: str) -> None: ...

    def append(self, path: str, content: str) -> None: ...

    def exists(self, path: str) -> bool: ...

    def remove(self, path: str) -> None: ...

    def list_dir(self, path: str) -> list[str]: ...

    def is_dir(self, path: str) -> bool: ...

    def is_file(self, path: str) -> bool: ...

    def absolute_path(self, path: str) -> str: ...

    def read_lines(
        self,
        path: str,
        *,
        start: int | None = None,
        end: int | None = None,
        head: int | None = None,
        tail: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[str, dict[str, object]]: ...

    def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[str, dict[str, object]]: ...

    def stat(self, path: str) -> dict[str, object]: ...

    def mkdirs(self, path: str) -> None: ...

    def move(self, src: str, dest: str, *, overwrite: bool = False) -> None: ...

    def copy(self, src: str, dest: str, *, overwrite: bool = False) -> None: ...

    def delete(self, path: str, *, recursive: bool = False) -> None: ...

    def allowed_roots(self) -> list[str]: ...

    def iter_files(self, base: str) -> tuple[str, ...]: ...

    explore_index: object | None


__all__ = ["Workspace", "WorkspaceSnapshot"]
