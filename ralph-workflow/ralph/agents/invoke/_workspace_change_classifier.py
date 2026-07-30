"""Workspace change classifier for activity-aware idle watchdog.

Classifies a workspace file change into one of five ``WorkspaceChangeKind``
categories and returns a binary weight:

- ``source``    - source code or other meaningful content (default weight 1.0)
- ``log``       - log, temp, swap, or compiled artifact (default weight 0.0)
- ``cache``     - well-known cache / vendored / venv / sentinel directory
                  (default weight 0.0)
- ``artifact``  - well-known artifact directory (default weight 0.0)
- ``other``     - anything that does not match a specific rule (default 0.0)

The rule order is fixed (see ``WorkspaceChangeClassifier.classify``) and
enumerated in the docstring of that method. The ``.agent`` top-level
directory is intentionally NOT in ``CACHE_PARENT_DIRS``; only its internal
temp / raw / completion-sentinel sub-paths are CACHE, while the
``.agent/artifacts`` sub-path is ARTIFACT. This keeps
``.agent/artifacts/plan.md`` as ARTIFACT and ``.agent/tmp/foo.log`` as
CACHE.

Weight semantics are BINARY: ``weight == 0.0`` means the change is
DROPPED (it does not count as workspace activity for the
NO_OUTPUT_DEADLINE verdict). ``weight == 1.0`` means the change is FULL
activity. Intermediate values are rejected by the validator today
(future fractional-TTL feature).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from ralph.agents.idle_watchdog._workspace_change_kind import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeKind,
)

#: Re-exported for callers that imported the enum from the classifier
#: module before the enum moved to its canonical home. New code should
#: import ``WorkspaceChangeKind`` from
#: ``ralph.agents.idle_watchdog._workspace_change_kind`` (the leaf
#: module that owns the canonical enum and default-weights dict).
__all__ = [
    "ARTIFACT_PARENT_DIRS",
    "CACHE_FILENAME_GLOBS",
    "CACHE_PARENT_DIRS",
    "DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS",
    "LOG_SUFFIXES",
    "SOURCE_EXTENSIONS",
    "WorkspaceChangeClassifier",
    "WorkspaceChangeKind",
    "_is_agent_internal_state_db_path",
    "_normalize_workspace_change_weights",
]


_CACHED_VALUES: frozenset[str] = frozenset(member.value for member in WorkspaceChangeKind)
_ALLOWED_WEIGHTS: frozenset[float] = frozenset({0.0, 1.0})

#: Parent directories that mark a path as CACHE (i.e. NOT activity for the
#: NO_OUTPUT_DEADLINE verdict). The ``.agent`` top-level is intentionally
#: absent; only its specific temp / raw / completion-sentinel sub-paths
#: are listed so the ``.agent/artifacts`` sub-path can be classified as
#: ARTIFACT (the rule order in ``classify`` checks CACHE first, then
#: ARTIFACT, so ``.agent/tmp`` and ``.agent/raw`` are correctly CACHE
#: while ``.agent/artifacts/plan.md`` is correctly ARTIFACT).
CACHE_PARENT_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        ".agent/tmp",
        ".agent/raw",
    }
)

#: Basename glob patterns for CACHE files. Matched against the
#: ``PurePosixPath`` basename (no directory portion) using fnmatch.
#:
#: Note: the engine-internal ``.agent/state.db`` trio (the WAL-mode
#: SQLite store and its ``-wal`` / ``-shm`` siblings) is NOT in this
#: tuple. Treating those basenames as CACHE-by-basename would
#: incorrectly drop user files matching the same name (e.g.
#: ``/repo/src/state.db``, ``/repo/docs/state.db-wal``). The trio is
#: matched by the path-scoped rule ``_is_agent_internal_state_db_path``
#: instead, which requires the parent directory to be ``.agent``.
CACHE_FILENAME_GLOBS: tuple[str, ...] = ("completion_seen_*.json",)

#: Exact basenames for the engine-internal ``.agent/state.db`` trio.
#: Used only by ``_is_agent_internal_state_db_path`` to scope CACHE
#: matching to ``.agent/state.db*`` so user files with the same
#: basename fall through to the OTHER/SOURCE rules. The WAL-mode
#: SQLite store is engine-internal bookkeeping, not user data.
_AGENT_INTERNAL_STATE_DB_BASENAMES: frozenset[str] = frozenset(
    {
        "state.db",
        "state.db-wal",
        "state.db-shm",
    }
)

#: Parent directories that mark a path as ARTIFACT (i.e. NOT activity
#: for the NO_OUTPUT_DEADLINE verdict). Checked AFTER CACHE so internal
#: CACHE sub-paths of an artifact tree still win.
ARTIFACT_PARENT_DIRS: frozenset[str] = frozenset({".agent/artifacts"})

#: Suffixes that mark a path as LOG. Checked after CACHE and ARTIFACT
#: so a ``.agent/artifacts/foo.log`` is still ARTIFACT.
LOG_SUFFIXES: frozenset[str] = frozenset({".log", ".tmp", ".bak", ".swp", "~", ".pyc", ".pyo"})

#: Extensions that mark a path as SOURCE (i.e. full activity). Checked
#: after CACHE / ARTIFACT / LOG. The default policy is conservative:
#: only well-known source extensions count.
SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".pyw",
        ".rs",
        ".go",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".java",
        ".kt",
        ".swift",
        ".m",
        ".mm",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".cs",
        ".rb",
        ".php",
        ".scala",
        ".clj",
        ".cljs",
        ".ex",
        ".exs",
        ".erl",
        ".hs",
        ".lua",
        ".r",
        ".R",
        ".jl",
        ".dart",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".vue",
        ".svelte",
        ".astro",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".xml",
        ".md",
        ".rst",
        ".txt",
        ".ini",
        ".cfg",
        ".conf",
        ".env",
        ".tf",
        ".proto",
    }
)


@dataclass(frozen=True)
class WorkspaceChangeClassifier:
    """Classifies workspace file changes for activity-aware idle verdict.

    The classifier is a frozen dataclass with a single ``weights`` field
    mapping each ``WorkspaceChangeKind`` string value to a binary
    weight (``0.0`` = drop, ``1.0`` = full activity). The validator
    rejects any key not in the five canonical ``WorkspaceChangeKind``
    string values AND any value not in ``{0.0, 1.0}``; intermediate
    weights are reserved for a future fractional-TTL feature and are
    rejected today.

    The classifier is stateful-free: ``classify`` is a pure function of
    its arguments. The only input that varies per call is the source
    path; ``workspace_root`` is optional and is reserved for a future
    "is this path inside the workspace" pre-check that would let the
    caller pass a workspace-relative path. The default rule order does
    not consult ``workspace_root`` today.
    """

    weights: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)
    )

    def __post_init__(self) -> None:
        for key, value in self.weights.items():
            if key not in _CACHED_VALUES:
                msg = (
                    f"WorkspaceChangeClassifier.weights[{key!r}] is not a valid"
                    f" WorkspaceChangeKind value; allowed: {sorted(_CACHED_VALUES)}"
                )
                raise ValueError(msg)
            if value not in _ALLOWED_WEIGHTS:
                msg = (
                    f"WorkspaceChangeClassifier.weights[{key!r}]={value!r}"
                    f" is not a binary weight; allowed: {{0.0, 1.0}}"
                )
                raise ValueError(msg)

    def classify(
        self,
        src_path: str,
        *,
        workspace_root: PurePosixPath | None = None,
    ) -> tuple[WorkspaceChangeKind, float]:
        """Classify a workspace file change.

        The rule order is fixed and is the only place in the codebase
        that decides what counts as workspace activity:

        1. **CACHE parent walk** - if any parent directory of the path
           (after POSIX normalization) is in ``CACHE_PARENT_DIRS``,
           return ``(CACHE, 0.0)``. The ``.agent`` top-level is
           intentionally NOT in the set; only ``.agent/tmp`` and
           ``.agent/raw`` are.
        2. **CACHE filename glob** - if the basename matches
           ``completion_seen_*.json`` (or any future glob in
           ``CACHE_FILENAME_GLOBS``), return ``(CACHE, 0.0)``.
        3. **CACHE agent-internal state.db trio** - if the basename is
           ``state.db``, ``state.db-wal``, or ``state.db-shm`` AND the
           immediate parent directory is exactly ``.agent``, return
           ``(CACHE, 0.0)``. This scopes the WAL-mode SQLite store to
           the engine-internal bookkeeping path so a user file at
           ``/repo/src/state.db`` falls through to OTHER/SOURCE rather
           than being incorrectly dropped as CACHE.
        4. **ARTIFACT parent walk** - if any parent directory is in
           ``ARTIFACT_PARENT_DIRS`` (= ``{".agent/artifacts"}``),
           return ``(ARTIFACT, 0.0)``. ARTIFACT is checked AFTER
           CACHE so an internal temp subdir of an artifact tree
           still wins.
        5. **LOG name/extension** - if the basename ends with any
           suffix in ``LOG_SUFFIXES`` (``*.log``, ``*.tmp``,
           ``*.bak``, ``*.swp``, ``*~``, ``*.pyc``, ``*.pyo``),
           return ``(LOG, 0.0)``.
        6. **SOURCE extension** - if the basename ends with any
           extension in ``SOURCE_EXTENSIONS``, return
           ``(SOURCE, weights["source"])``. The default weight is
           ``1.0`` so source-code changes count as full activity.
        7. **OTHER** - return ``(OTHER, weights["other"])``. The
           default weight is ``0.0`` so unmatched paths are dropped.

        Args:
            src_path: The path to classify. POSIX separators are
                assumed; the production watchdog observer emits
                POSIX-formatted paths on every supported host.
            workspace_root: Reserved for a future "is this path
                inside the workspace" pre-check; the default rule
                order does not consult it.

        Returns:
            ``(kind, weight)``. The weight is taken from the
            classifier's ``weights`` mapping (default
            ``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS``).
        """
        # Normalize the path to POSIX form. The watchdog observer
        # already emits POSIX paths on every supported host, so this
        # is a defensive normalization for callers that pass
        # OS-native paths.
        posix = PurePosixPath(src_path.replace("\\", "/"))
        parts = posix.parts
        basename = posix.name

        # The rule order is fixed (see the docstring). Each tuple is
        # (predicate, kind); the first matching predicate wins. Keeping
        # the rules in a list lets ``classify`` return at most once,
        # which satisfies the PLR0911 complexity ceiling while still
        # preserving the documented 7-rule evaluation order.
        rules: tuple[tuple[bool, WorkspaceChangeKind], ...] = (
            # (1) CACHE parent walk. Match every directory in the path
            # against CACHE_PARENT_DIRS as either a single-part name
            # (``/.git``) or a multi-part name (``/.agent/tmp``). The
            # single-part match lets ``/repo/.git/HEAD`` resolve to
            # CACHE; the multi-part match lets ``/repo/.agent/tmp/foo``
            # resolve to CACHE without putting the ``.agent`` top-level
            # itself into the CACHE set (which would have made
            # ``.agent/artifacts/plan.md`` unreachable as ARTIFACT).
            (
                _matches_parent_walk(parts, CACHE_PARENT_DIRS),
                WorkspaceChangeKind.CACHE,
            ),
            # (2) CACHE filename glob
            (
                _matches_filename_glob(basename, CACHE_FILENAME_GLOBS),
                WorkspaceChangeKind.CACHE,
            ),
            # (3) CACHE agent-internal state.db trio. Scoped to
            # ``.agent/`` parent so user files matching the basename
            # (``/repo/src/state.db``, ``/repo/docs/state.db-wal``) are
            # NOT classified CACHE. Uses the in-memory
            # ``_AGENT_INTERNAL_STATE_DB_BASENAMES`` set so an unknown
            # suffix (e.g. ``state.db-journal``) is correctly NOT CACHE
            # unless explicitly listed.
            (
                _is_agent_internal_state_db_path(parts, basename),
                WorkspaceChangeKind.CACHE,
            ),
            # (4) ARTIFACT parent walk. Same windowed-match semantics as
            # CACHE so ``.agent/artifacts/plan.md`` matches
            # ``.agent/artifacts`` (an explicit two-part entry).
            (
                _matches_parent_walk(parts, ARTIFACT_PARENT_DIRS),
                WorkspaceChangeKind.ARTIFACT,
            ),
            # (5) LOG name/extension
            (
                _matches_suffix(basename, LOG_SUFFIXES),
                WorkspaceChangeKind.LOG,
            ),
            # (6) SOURCE extension
            (
                _matches_suffix(basename, SOURCE_EXTENSIONS),
                WorkspaceChangeKind.SOURCE,
            ),
        )

        for predicate, kind in rules:
            if predicate:
                return kind, self._weight(kind)

        # (7) OTHER — default fallback when no rule matches.
        return WorkspaceChangeKind.OTHER, self._weight(WorkspaceChangeKind.OTHER)

    def _weight(self, kind: WorkspaceChangeKind) -> float:
        """Look up the weight for a kind from the classifier's mapping.

        The validator guarantees the key is present and the value is
        binary, so this is a simple dict lookup with a default
        fallback for ``OTHER`` (defensive: the validator also
        guarantees ``other`` is present in the dict).
        """
        return self.weights.get(kind.value, 0.0)


def _normalize_workspace_change_weights(
    partial: Mapping[str, float] | None,
) -> dict[str, float]:
    """Merge a partial operator-supplied weights dict over the defaults.

    Operators typically supply only the keys they want to opt in
    (e.g. ``{"log": 1.0, "source": 1.0}``); missing keys are filled
    in from ``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS``. Unknown keys
    and non-binary values are caught downstream by
    ``WorkspaceChangeClassifier.__post_init__``.

    Returns a fresh dict so the caller can mutate without affecting
    the global default.
    """
    merged: dict[str, float] = dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)
    if partial is not None:
        merged.update(partial)
    return merged


def _is_agent_internal_state_db_path(
    path_parts: tuple[str, ...],
    basename: str,
) -> bool:
    """Return True iff the path is the engine-internal ``.agent/state.db`` trio.

    The WAL-mode SQLite store at ``.agent/state.db`` and its
    ``-wal`` / ``-shm`` siblings are engine-internal bookkeeping
    and MUST be classified CACHE so the idle watchdog ignores their
    writes. The basename alone is not enough: a user file at
    ``/repo/src/state.db`` or ``/repo/docs/state.db-wal`` is NOT
    bookkeeping and must fall through to OTHER/SOURCE so it counts
    as workspace activity. The scoped check is:

    - The basename is exactly one of ``state.db``, ``state.db-wal``,
      ``state.db-shm`` (looked up in
      ``_AGENT_INTERNAL_STATE_DB_BASENAMES``).
    - The immediate parent directory is exactly ``.agent``.

    Args:
        path_parts: The POSIX path components (``PurePosixPath.parts``)
            of the source path. Used to inspect the immediate parent.
        basename: The POSIX path basename (``PurePosixPath.name``).

    Returns:
        ``True`` when both conditions hold; ``False`` otherwise
        (including when the path has no parent or when the basename
        is not in the trio set).
    """
    if basename not in _AGENT_INTERNAL_STATE_DB_BASENAMES:
        return False
    if len(path_parts) < _PARENT_DIR_LOOKUP_DEPTH:
        return False
    return path_parts[-_PARENT_DIR_LOOKUP_DEPTH] == ".agent"


#: Depth into ``PurePosixPath.parts`` at which the immediate parent
#: directory lives. Used by ``_is_agent_internal_state_db_path`` and
#: ``_matches_parent_walk`` so the magic number ``2`` is named in
#: one place rather than scattered across helpers.
_PARENT_DIR_LOOKUP_DEPTH: int = 2


def _matches_parent_walk(
    path_parts: tuple[str, ...],
    parent_set: frozenset[str],
) -> bool:
    """Return True iff any windowed substring of ``path_parts`` matches ``parent_set``.

    ``CACHE_PARENT_DIRS`` and ``ARTIFACT_PARENT_DIRS`` are matched as
    either a single-part name (``/.git``) or a multi-part name
    (``/.agent/tmp``). The single-part match lets ``/repo/.git/HEAD``
    resolve to CACHE; the multi-part match lets
    ``/repo/.agent/tmp/foo`` resolve to CACHE without putting the
    ``.agent`` top-level itself into the CACHE set (which would
    have made ``.agent/artifacts/plan.md`` unreachable as
    ARTIFACT).
    """
    for window_size in range(1, len(path_parts) + 1):
        for start in range(len(path_parts) - window_size + 1):
            candidate = "/".join(path_parts[start : start + window_size])
            if candidate in parent_set:
                return True
    return False


def _matches_filename_glob(basename: str, globs: tuple[str, ...]) -> bool:
    """Return True iff ``basename`` matches any glob in ``globs``."""
    return any(fnmatch.fnmatch(basename, glob) for glob in globs)


def _matches_suffix(basename: str, suffixes: frozenset[str]) -> bool:
    """Return True iff ``basename`` ends with any string in ``suffixes``."""
    return any(basename.endswith(suffix) for suffix in suffixes)
