
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_EXEC_URI_PREFIX = "ralph://exec/"
_URI_PATTERN = re.compile(r"^ralph://exec/([A-Za-z0-9_\-.]+)$")
_BASENAME_PATTERN = re.compile(r"^ralph-exec-[A-Za-z0-9_\-.]+\.txt$")
MAX_READ_BYTES: int = 4 * 1024 * 1024
_MAX_RESOLVER_ENTRIES: int = 256


def build_exec_uri(spill_name: str) -> str:
    if not _BASENAME_PATTERN.match(spill_name):
        raise ValueError(f"Invalid exec spill basename: {spill_name!r}")
    return f"{_EXEC_URI_PREFIX}{spill_name}"


def parse_exec_uri(uri: str) -> str | None:
    m = _URI_PATTERN.match(uri)
    if m is None:
        return None
    name = m.group(1)
    if not _BASENAME_PATTERN.match(name):
        return None
    if ".." in name or "/" in name:
        return None
    return name


@dataclass(frozen=True, slots=True)
class ExecResourceEntry:

    uri: str
    name: str
    spill_path: Path
    size_bytes: int
    mime_type: str

    def resource_list_entry(self) -> dict[str, object]:
        return {
            "uri": self.uri,
            "name": self.name,
            "mimeType": self.mime_type,
            "size": self.size_bytes,
        }


class ExecResourceResolver:

    def register(self, spill_path: Path) -> str:
        spill_path = spill_path.resolve()
        if not self._is_under_trusted_root(spill_path):
            raise ValueError(f"exec spill path escapes trusted roots: {spill_path}")
        name = spill_path.name
        if not _BASENAME_PATTERN.match(name):
            raise ValueError(f"Invalid exec spill basename: {name!r}")
        uri = build_exec_uri(name)
        size = spill_path.stat().st_size if spill_path.exists() else 0
        entry = ExecResourceEntry(
            uri=uri,
            name=name,
            spill_path=spill_path,
            size_bytes=size,
            mime_type="text/plain",
        )
        self._entries[uri] = entry
        while len(self._entries) > _MAX_RESOLVER_ENTRIES:
            self._entries.popitem(last=False)
        return uri

    def __init__(self, spill_roots: tuple[Path, ...]) -> None:
        self._roots: tuple[Path, ...] = tuple(p.resolve() for p in spill_roots)
        self._entries: OrderedDict[str, ExecResourceEntry] = (
            OrderedDict()
        )

    @property
    def spill_roots(self) -> tuple[Path, ...]:
        return self._roots

    def add_root(self, root: Path) -> None:
        resolved = root.resolve()
        if resolved in self._roots:
            return
        self._roots = (*self._roots, resolved)

    def list_entries(self) -> tuple[ExecResourceEntry, ...]:
        return tuple(self._entries.values())

    def read(self, uri: str) -> tuple[bytes, str, int] | None:
        name = parse_exec_uri(uri)
        if name is None:
            return None
        cached = self._entries.get(uri)
        if cached is not None and cached.spill_path.exists():
            return self._read_bounded(cached.spill_path)
        for root in self._roots:
            candidate = root / name
            try:
                if not self._is_under_trusted_root(candidate):
                    continue
            except (OSError, ValueError):
                continue
            if candidate.is_file():
                return self._read_bounded(candidate)
        return None

    def _is_under_trusted_root(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return False
        for root in self._roots:
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return True
        return False

    def _read_bounded(self, path: Path) -> tuple[bytes, str, int] | None:
        try:
            total = path.stat().st_size
        except OSError:
            return None
        with path.open("rb") as handle:
            data = handle.read(MAX_READ_BYTES)
        return data, "text/plain", total


__all__ = (
    "MAX_READ_BYTES",
    "ExecResourceEntry",
    "ExecResourceResolver",
    "build_exec_uri",
    "parse_exec_uri",
)
