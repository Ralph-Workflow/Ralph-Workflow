"""Shared state and exception types for the indexed exploration reindex pipeline.

Extracted from :mod:`ralph.mcp.explore.pipeline` so the pipeline
sub-modules can import state types without re-entering the pipeline
hub. The hub re-exports these symbols for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from ralph.mcp.explore.store import Clock

EXTRACTOR_VERSION: Final[str] = "phase1-lexical-v1"

DEFAULT_TIMEOUT_MS: Final[int] = 5_000
DEFAULT_FULL_TIMEOUT_MS: Final[int] = 60_000


@dataclass(frozen=True, slots=True)
class ReindexResult:
    """Result of a reindex invocation."""

    job_id: str
    generation: int
    status: str
    changed_files: tuple[str, ...] = ()
    failed_files: tuple[str, ...] = ()
    parse_count: int = 0
    dirty_paths_count: int = 0
    elapsed_seconds: float = 0.0
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class ReindexOptions:
    """Options for a reindex call."""

    mode: str = "changed"
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    path_scope: tuple[str, ...] = ()
    clock: Clock | None = None


class FileReadError(Exception):
    """A file failed to read during reindex."""


@dataclass
class _ReindexState:
    """Mutable per-job scratch state."""

    job_id: str
    started_at: float
    deadline: float
    deadline_ms: int = 0
    parse_count: int = 0
    dirty_paths_count: int = 0
    changed_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    error_summary: str | None = None
    timed_out: bool = False


class _ReindexTimeoutError(Exception):
    pass


class _ReindexCancelledError(Exception):
    """Raised by the reindex writer when the cancel callable returns True."""

    pass
