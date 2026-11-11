
from __future__ import annotations

from pathlib import Path, PurePosixPath

from ralph.workspace._snapshot import WorkspaceSnapshot
from ralph.workspace.skip import RECURSIVE_SKIP_DIRECTORY_NAMES


class MemoryWorkspace:

    def __init__(self, root: str = "/workspace") -> None:
        self._storage: dict[
            str, str
        ] = {}
        self._dirs: set[str] = {""}
        self._root = PurePosixPath(root)
        self.explore_index: object | None = None

    @property
    def root(self) -> Path:
        return Path(str(self._root))

    @root.setter
    def root(self, value: Path | str) -> None:
        self._root = PurePosixPath(value)

    def _normalize(self, path: str) -> str:
        normalized = str(PurePosixPath(path))
        return "" if normalized == "." else normalized

    def _ensure_parent(self, path: str) -> None:
        p = PurePosixPath(path)
        if p.parent == PurePosixPath("."):
            return
        for parent in reversed(p.parents[:-1]):
            normalized_parent = self._normalize(str(parent))
            if normalized_parent:
                self._dirs.add(normalized_parent)

    def read(self, path: str) -> str:
        normalized = self._normalize(path)
        if normalized not in self._storage:
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)
        return self._storage[normalized]

    def snapshot(self, path: str, *, max_bytes: int | None = None) -> WorkspaceSnapshot:
        normalized = self._normalize(path)
        if normalized in self._dirs:
            return WorkspaceSnapshot(
                stat={
                    "type": "dir",
                    "size_bytes": 0,
                    "created_unix": None,
                    "modified_unix": None,
                    "mode": None,
                },
                content=None,
            )
        content = self._storage.get(normalized)
        if content is not None:
            size_bytes = len(content.encode("utf-8"))
            return WorkspaceSnapshot(
                stat={
                    "type": "file",
                    "size_bytes": size_bytes,
                    "created_unix": None,
                    "modified_unix": None,
                    "mode": None,
                },
                content=content if max_bytes is None or size_bytes <= max_bytes else None,
            )
        return WorkspaceSnapshot(stat={"type": "missing"}, content=None)

    def write(self, path: str, content: str) -> None:
        normalized = self._normalize(path)
        self._ensure_parent(normalized)
        self._storage[normalized] = content

    def append(self, path: str, content: str) -> None:
        normalized = self._normalize(path)
        self._ensure_parent(normalized)
        if normalized in self._storage:
            self._storage[normalized] += content
        else:
            self._storage[normalized] = content

    def exists(self, path: str) -> bool:
        return self._normalize(path) in self._storage

    def remove(self, path: str) -> None:
        normalized = self._normalize(path)
        self._storage.pop(normalized, None)

    def list_dir(self, path: str) -> list[str]:
        normalized = self._normalize(path)
        if normalized and not normalized.endswith("/"):
            normalized += "/"

        result: list[str] = []
        for key in self._storage:
            if key == normalized:
                continue
            if normalized == "" or key.startswith(normalized):
                remainder = key[len(normalized) :]
                first_part = remainder.split("/")[0]
                if first_part and first_part not in result:
                    result.append(first_part)

        for d in self._dirs:
            if normalized == "" or d.startswith(normalized):
                if d == normalized:
                    continue
                if normalized == "" or d.startswith(normalized):
                    remainder = d[len(normalized) :].lstrip("/")
                    first_part = remainder.split("/")[0]
                    if first_part and first_part not in result:
                        result.append(first_part)

        return sorted(result)

    def is_dir(self, path: str) -> bool:
        normalized = self._normalize(path)
        return normalized in self._dirs

    def is_file(self, path: str) -> bool:
        return self.exists(path)

    def clear(self) -> None:
        self._storage.clear()
        self._dirs = {""}

    def create_dir(self, path: str) -> None:
        normalized = self._normalize(path)
        if normalized:
            self._dirs.add(normalized)

    def absolute_path(self, path: str) -> str:
        return str(self._root / self._normalize(path))

    def read_lines(
        self,
        path: str,
        *,
        start: int | None = None,
        end: int | None = None,
        head: int | None = None,
        tail: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[str, dict[str, object]]:
        has_range = (start is not None) or (end is not None)
        has_head = head is not None
        has_tail = tail is not None
        mode_count = sum(1 for m in (has_range, has_head, has_tail) if m)
        if mode_count > 1:
            raise ValueError("Only one of (start/end range), head, or tail may be specified")

        content = self.read(path)
        content_bytes = len(content.encode("utf-8"))
        if mode_count == 0 and max_bytes is not None and content_bytes > max_bytes:
            raise ValueError(
                f"File too large for read_lines: {content_bytes} bytes exceeds "
                f"limit of {max_bytes} bytes (path={path!r}). "
                "Use a partial read (head/tail/range) or raise max_bytes."
            )
        all_lines = content.splitlines(keepends=True)
        total_lines = len(all_lines)
        returned_lines: list[str]
        truncated = False

        if head is not None:
            returned_lines = all_lines[:head]
            if total_lines > head:
                truncated = True
        elif tail is not None:
            returned_lines = [] if tail == 0 else all_lines[-tail:]
            if total_lines > tail:
                truncated = True
        elif start is not None or end is not None:
            start_idx = (start - 1) if start is not None else 0
            end_idx = end if end is not None else total_lines
            start_idx = max(0, start_idx)
            end_idx = min(total_lines, end_idx)
            returned_lines = all_lines[start_idx:end_idx]
            if end_idx < total_lines:
                truncated = True
        else:
            returned_lines = all_lines

        return "".join(returned_lines), {
            "total_lines": total_lines,
            "returned_lines": len(returned_lines),
            "truncated": truncated,
        }

    def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[str, dict[str, object]]:
        if offset < 0:
            raise ValueError("offset must not be negative")
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")

        normalized = self._normalize(path)
        if normalized not in self._storage:
            raise FileNotFoundError(f"File not found: {path}")
        raw = self._storage[normalized].encode("utf-8")
        total_bytes = len(raw)
        available_bytes = max(0, total_bytes - offset)
        requested_bytes = available_bytes if limit is None else min(available_bytes, limit)
        if max_bytes is not None and requested_bytes > max_bytes:
            raise ValueError(
                f"File too large for read_bytes: requested {requested_bytes} bytes exceeds "
                f"limit of {max_bytes} bytes (path={path!r}). "
                "Use offset/limit to request a bounded window or raise max_bytes."
            )
        sliced = raw[offset : offset + limit] if limit is not None else raw[offset:]
        returned_bytes = len(sliced)
        truncated = (offset + returned_bytes) < total_bytes
        text = sliced.decode("utf-8")
        return text, {
            "total_bytes": total_bytes,
            "returned_bytes": returned_bytes,
            "truncated": truncated,
        }

    def stat(self, path: str) -> dict[str, object]:
        normalized = self._normalize(path)
        if normalized in self._dirs:
            return {
                "type": "dir",
                "size_bytes": 0,
                "created_unix": None,
                "modified_unix": None,
                "mode": None,
            }
        if normalized in self._storage:
            return {
                "type": "file",
                "size_bytes": len(self._storage[normalized].encode("utf-8")),
                "created_unix": None,
                "modified_unix": None,
                "mode": None,
            }
        return {"type": "missing"}

    def mkdirs(self, path: str) -> None:
        normalized = self._normalize(path)
        if normalized:
            parts = normalized.split("/")
            for i in range(len(parts)):
                self._dirs.add("/".join(parts[: i + 1]))

    def move(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        src_norm = self._normalize(src)
        dest_norm = self._normalize(dest)
        if (dest_norm in self._storage or dest_norm in self._dirs) and not overwrite:
            raise FileExistsError(f"Destination '{dest}' already exists")
        if src_norm in self._storage:
            self._storage[dest_norm] = self._storage.pop(src_norm)
        elif src_norm in self._dirs:
            self._dirs.discard(src_norm)
            self._dirs.add(dest_norm)
        else:
            raise FileNotFoundError(f"Source '{src}' not found")

    def copy(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        src_norm = self._normalize(src)
        dest_norm = self._normalize(dest)
        if (dest_norm in self._storage or dest_norm in self._dirs) and not overwrite:
            raise FileExistsError(f"Destination '{dest}' already exists")
        if src_norm in self._storage:
            self._storage[dest_norm] = self._storage[src_norm]
        elif src_norm in self._dirs:
            self._dirs.add(dest_norm)
        else:
            raise FileNotFoundError(f"Source '{src}' not found")

    def delete(self, path: str, *, recursive: bool = False) -> None:
        normalized = self._normalize(path)
        if normalized in self._dirs:
            if not recursive:
                raise IsADirectoryError(f"Path '{path}' is a directory, use recursive=True")
            self._dirs.discard(normalized)
            self._storage.pop(normalized, None)
        elif normalized in self._storage:
            del self._storage[normalized]
        else:
            raise FileNotFoundError(f"Path '{path}' not found")

    def allowed_roots(self) -> list[str]:
        return [str(self._root)]

    def iter_files(self, base: str) -> tuple[str, ...]:
        normalized = self._normalize(base)
        base_parts = PurePosixPath(normalized).parts
        if any(part in RECURSIVE_SKIP_DIRECTORY_NAMES for part in base_parts):
            return ()
        if normalized and not normalized.endswith("/"):
            normalized += "/"

        results: list[str] = []
        for key in self._storage:
            if normalized == "" or key.startswith(normalized):
                remainder = key[len(normalized) :]
                parts = remainder.split("/")
                if len(parts) == 1 or parts[0] not in RECURSIVE_SKIP_DIRECTORY_NAMES:
                    results.append(key)

        return tuple(results)
