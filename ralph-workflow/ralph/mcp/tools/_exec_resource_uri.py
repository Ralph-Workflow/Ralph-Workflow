"""URI builder/parser and resolver for ``ralph://exec/`` resources.

AC-11: the exec summary output reports ``stdout_resource_id`` /
``stderr_resource_id`` of the form ``ralph://exec/<spill-name>``. The
URIs must be replayable through ``resources/read`` on the MCP server,
so a registered resolver walks the workspace's exec spill directory
and returns the file content as a base64-encoded blob bounded by the
retention cap.

Path-traversal hardening: the resolver only accepts spill names that
match a strict pattern (lowercase letters, digits, dash, underscore,
dot, the literal ``ralph-exec-`` prefix) and refuses any name that
contains ``/`` or ``..`` or that escapes the configured spill root.
URIs for unknown or expired spill files are rejected with the same
structured error as ``ralph://media/`` unknown artifacts.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# The ``spill_output`` helper in ``_exec_output_spill.py`` writes
# files with this prefix and ``.txt`` suffix. Keeping the URI pattern
# tight to that contract means a malicious caller cannot coax the
# resolver into reading arbitrary files under the spill root.
_EXEC_URI_PREFIX = "ralph://exec/"
_URI_PATTERN = re.compile(r"^ralph://exec/([A-Za-z0-9_\-.]+)$")
_BASENAME_PATTERN = re.compile(r"^ralph-exec-[A-Za-z0-9_\-.]+\.txt$")
#: Maximum spill size returned via ``resources/read`` (4 MiB). Larger
#: spills are truncated with a structured error so the replayed
#: resource cannot itself flood the agent's context window.
MAX_READ_BYTES: int = 4 * 1024 * 1024
#: Bounded cache for the resolver's resource list output (it is
#: bounded-accumulator-ok: the OrderedDict's cap is enforced by
#: ``_MAX_RESOLVER_ENTRIES``; once full, oldest entries are dropped
#: on insertion).
_MAX_RESOLVER_ENTRIES: int = 256


def build_exec_uri(spill_name: str) -> str:
    """Build a ``ralph://exec/<spill-name>`` URI from a file basename.

    The basename is validated against the contract pattern; raising
    here is correct because every caller is a workspace-internal
    helper that controls the spill filename.
    """
    if not _BASENAME_PATTERN.match(spill_name):
        raise ValueError(f"Invalid exec spill basename: {spill_name!r}")
    return f"{_EXEC_URI_PREFIX}{spill_name}"


def parse_exec_uri(uri: str) -> str | None:
    """Parse a ``ralph://exec/<spill-name>`` URI and return the name.

    Returns ``None`` when the URI does not match the expected
    pattern. Path-traversal sequences (``..``) and slash characters
    are rejected by the pattern itself.
    """
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
    """A single ``ralph://exec/<spill-name>`` resource entry."""

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
    """Resolver for ``ralph://exec/<spill-name>`` resources.

    The resolver owns a set of trusted spill directories (typically
    the workspace's ``.agent/tmp`` directory plus any registered
    extra spill roots). On a ``read`` call, it parses the URI,
    locates the file under a trusted root, and returns its bytes
    base64-encoded. Files outside a trusted root are rejected.

    Ponytail: the resolver is intentionally cheap and stateless
    aside from the trusted roots. The bounded manifest cache here is
    for ``resources/list`` only; reads always go through the live
    filesystem so an evicted cache entry can still be replayed until
    the file is pruned by the spill cache retention policy.
    """

    # ``register`` is intentionally typed as accepting ``Path``; the
    # :class:`ralph.mcp.tools._exec_resource_protocol.ExecResourceResolverLike`
    # protocol widens the parameter to ``object`` so test doubles can
    # pass strings. The concrete implementation only accepts ``Path``
    # values; the protocol's permissive type stays a pure consumer-
    # side contract. This explicit override keeps the class's public
    # surface narrow (a real Path) without losing the mypy-typed
    # protocol membership that callers depend on.
    def register(self, spill_path: Path) -> str:
        """Register a spill file and return its replayable URI."""
        spill_path = spill_path.resolve()
        if not self._is_under_trusted_root(spill_path):
            raise ValueError(
                f"exec spill path escapes trusted roots: {spill_path}"
            )
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
        # bounded-accumulator-ok: cap the cache and drop the oldest.
        while len(self._entries) > _MAX_RESOLVER_ENTRIES:
            self._entries.popitem(last=False)
        return uri

    def __init__(self, spill_roots: tuple[Path, ...]) -> None:
        self._roots: tuple[Path, ...] = tuple(p.resolve() for p in spill_roots)
        # bounded-accumulator-ok: _MAX_RESOLVER_ENTRIES=256 with FIFO eviction in register()
        self._entries: OrderedDict[str, ExecResourceEntry] = OrderedDict()  # bounded-accumulator-ok: cap=_MAX_RESOLVER_ENTRIES (256), popitem(last=False) eviction in register()

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
        """Return ``(bytes, mime_type, total_size)`` for ``uri`` or ``None``.

        Returns ``None`` when the URI does not match the contract, the
        underlying file is missing, or the path is not under a
        trusted spill root. Large spills are truncated to
        ``MAX_READ_BYTES``; callers should fall back to ``read_file``
        on the workspace for full retrieval.
        """
        name = parse_exec_uri(uri)
        if name is None:
            return None
        # Try the cache first so registered entries resolve even
        # when the resolver has been reinitialized across processes.
        cached = self._entries.get(uri)
        if cached is not None and cached.spill_path.exists():
            return self._read_bounded(cached.spill_path)
        # Fall back to scanning the trusted roots. The basename
        # check is the only safety net here.
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
