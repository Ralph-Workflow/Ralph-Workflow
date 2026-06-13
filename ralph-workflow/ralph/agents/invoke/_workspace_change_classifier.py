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
``.agent/artifacts/plan.json`` as ARTIFACT and ``.agent/tmp/foo.log`` as
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
#: while ``.agent/artifacts/plan.json`` is correctly ARTIFACT).
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
CACHE_FILENAME_GLOBS: tuple[str, ...] = ("completion_seen_*.json",)

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
        3. **ARTIFACT parent walk** - if any parent directory is in
           ``ARTIFACT_PARENT_DIRS`` (= ``{".agent/artifacts"}``),
           return ``(ARTIFACT, 0.0)``. ARTIFACT is checked AFTER
           CACHE so an internal temp subdir of an artifact tree
           still wins.
        4. **LOG name/extension** - if the basename ends with any
           suffix in ``LOG_SUFFIXES`` (``*.log``, ``*.tmp``,
           ``*.bak``, ``*.swp``, ``*~``, ``*.pyc``, ``*.pyo``),
           return ``(LOG, 0.0)``.
        5. **SOURCE extension** - if the basename ends with any
           extension in ``SOURCE_EXTENSIONS``, return
           ``(SOURCE, weights["source"])``. The default weight is
           ``1.0`` so source-code changes count as full activity.
        6. **OTHER** - return ``(OTHER, weights["other"])``. The
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

        # (1) CACHE parent walk. Match every directory in the path
        # against CACHE_PARENT_DIRS as either a single-part name
        # (``/.git``) or a multi-part name (``/.agent/tmp``). The
        # single-part match lets ``/repo/.git/HEAD`` resolve to
        # CACHE; the multi-part match lets ``/repo/.agent/tmp/foo``
        # resolve to CACHE without putting the ``.agent`` top-level
        # itself into the CACHE set (which would have made
        # ``.agent/artifacts/plan.json`` unreachable as ARTIFACT).
        cache_set = CACHE_PARENT_DIRS
        for window_size in range(1, len(parts) + 1):
            for start in range(len(parts) - window_size + 1):
                candidate = "/".join(parts[start : start + window_size])
                if candidate in cache_set:
                    return WorkspaceChangeKind.CACHE, self._weight(WorkspaceChangeKind.CACHE)

        basename = posix.name
        # (2) CACHE filename glob
        for glob in CACHE_FILENAME_GLOBS:
            if fnmatch.fnmatch(basename, glob):
                return WorkspaceChangeKind.CACHE, self._weight(WorkspaceChangeKind.CACHE)

        # (3) ARTIFACT parent walk. Same windowed-match semantics as
        # CACHE so ``.agent/artifacts/plan.json`` matches
        # ``.agent/artifacts`` (an explicit two-part entry).
        for window_size in range(1, len(parts) + 1):
            for start in range(len(parts) - window_size + 1):
                candidate = "/".join(parts[start : start + window_size])
                if candidate in ARTIFACT_PARENT_DIRS:
                    return WorkspaceChangeKind.ARTIFACT, self._weight(WorkspaceChangeKind.ARTIFACT)

        # (4) LOG name/extension
        for suffix in LOG_SUFFIXES:
            if basename.endswith(suffix):
                return WorkspaceChangeKind.LOG, self._weight(WorkspaceChangeKind.LOG)

        # (5) SOURCE extension
        for ext in SOURCE_EXTENSIONS:
            if basename.endswith(ext):
                return WorkspaceChangeKind.SOURCE, self._weight(WorkspaceChangeKind.SOURCE)

        # (6) OTHER
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
