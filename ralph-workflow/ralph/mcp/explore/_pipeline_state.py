
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

    mode: str = "changed"
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    path_scope: tuple[str, ...] = ()
    clock: Clock | None = None
    structure_extractor: str = "auto"


class FileReadError(Exception):
    pass


@dataclass
class _ReindexState:

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

    pass
