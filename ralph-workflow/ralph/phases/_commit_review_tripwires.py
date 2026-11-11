"""Deterministic Stage 1 tripwire evaluator for pending diffs.

This module evaluates proposed commit diffs against deterministic safety tripwires
before agent review. A hard tripwire rejects immediately without consulting an agent
and rolls back to HEAD/baseline. Softer signals are collected and passed to Stage 2
as structured evidence.

Tripwire categories:
- Hard:
  - Mass deletion: deleted tracked file count or fraction exceeds policy thresholds.
  - Protected deletion / overwrite: deletion or modification of .git content,
    project manifests/lockfiles, specification prompt, AGENTS.md, or top-level source/test directories.
- Soft:
  - Out of scope: modified/added files outside configured allowed_directories.
  - Staged secrets / recognized secret files: credentials, keys, or secret files.
  - Oversized binaries: binary files exceeding configured binary size limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

from ralph.phases._tripwire_finding import Finding
from ralph.phases._tripwire_report import TripwireReport
from ralph.phases.integrity import DEFAULT_PROMPT_PATH

DEFAULT_MAX_DELETED_FILES: Final[int] = 50
DEFAULT_MAX_DELETED_FRACTION: Final[float] = 0.25
DEFAULT_BINARY_SIZE_LIMIT: Final[int] = 1_000_000

DEFAULT_PROTECTED_DOCUMENTS: Final[frozenset[str]] = frozenset(
    {
        DEFAULT_PROMPT_PATH,
        "AGENTS.md",
    }
)

DEFAULT_LOCKFILE_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "Cargo.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        "pnpm-lock.yaml",
        "flake.lock",
        "bun.lockb",
        "bun.lock",
    }
)

DEFAULT_MANIFEST_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Gemfile",
        "Pipfile",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "flake.nix",
    }
)

DEFAULT_TOP_LEVEL_SOURCE_TEST_DIRS: Final[frozenset[str]] = frozenset(
    {
        "src",
        "tests",
        "test",
        "lib",
        "ralph",
        "app",
        "pkg",
    }
)

RECOGNIZED_SECRET_PATTERNS: Final[tuple[str, ...]] = (
    ".env",
    ".env.local",
    ".env.*.local",
    "secrets.yml",
    "credentials.json",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
)

BINARY_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".webp",
        ".svgz",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".iso",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".zst",
        ".pdf",
        ".whl",
        ".pyc",
        ".pyo",
        ".wasm",
    }
)

SECRET_CONTENT_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("AWS access key", re.compile(r"\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub personal access token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("Private key PEM header", re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*\b")),
)


@runtime_checkable
class _DiffEntryProtocol(Protocol):
    """Structural protocol for pending diff entries."""

    path: str
    status: str
    old_mode: str | None
    new_mode: str | None
    size: int
    is_binary: bool
    base_tree_paths: frozenset[str] | None


DiffEntryProtocol = _DiffEntryProtocol


@dataclass(frozen=True)
class TripwireThresholds:
    """Threshold configuration for deterministic tripwire evaluation.

    Attributes:
        max_deleted_files: Maximum allowed number of deleted files before triggering
            a mass-deletion hard tripwire. Defaults to 50.
        max_deleted_fraction: Maximum allowed ratio of deleted files to total tracked
            files (0.0 to 1.0) before triggering a mass-deletion hard tripwire.
            Defaults to 0.25 (25%).
        binary_size_limit: Maximum allowed size in bytes for binary files before
            triggering a binary size soft tripwire. Defaults to 1,000,000 bytes (1 MB).
    """

    max_deleted_files: int = DEFAULT_MAX_DELETED_FILES
    max_deleted_fraction: float = DEFAULT_MAX_DELETED_FRACTION
    binary_size_limit: int = DEFAULT_BINARY_SIZE_LIMIT


@dataclass(frozen=True)
class _DiffEntry:
    """Pending diff entry representation.

    Attributes:
        path: Repository-relative path of the file.
        status: Git status indicator ('A', 'M', 'D', 'R', etc.).
        old_mode: Octal file mode before change (e.g. '100644', '040000').
        new_mode: Octal file mode after change (e.g. '100644', '000000').
        size: Size of the file in bytes.
        is_binary: Whether the file is classified as binary.
        content: Optional file text or patch content (for content secret scanning).
        base_tree_paths: Optional tracked paths of the tree the diff was computed
            against. A recursive diff reports blob entries only, so a directory
            removal arrives as one deleted blob path per tracked file: the
            membership is what proves the directory is gone in its entirety.
    """

    path: str
    status: str
    old_mode: str | None = None
    new_mode: str | None = None
    size: int = 0
    is_binary: bool = False
    content: str | None = None
    base_tree_paths: frozenset[str] | None = None


DiffEntry = _DiffEntry


def _is_deleted(status: str) -> bool:
    """Return True if git status denotes deletion."""
    s = status.strip().upper()
    return s == "D" or s.startswith("D") or s == "DELETED"


def _is_git_path(norm: PurePosixPath) -> bool:
    """Return True if path represents .git repository internal content."""
    parts = norm.parts
    return len(parts) > 0 and parts[0] == ".git"


def is_recognized_secret_path(path: str) -> bool:
    """Return whether a repository-relative path names a recognized secret file."""
    norm = PurePosixPath(path.replace("\\", "/"))
    return any(fnmatchcase(norm.name, pat) for pat in RECOGNIZED_SECRET_PATTERNS)


def _get_entry_attr(entry: object, attr: str, default: object = None) -> object:
    """Extract attribute from object or dict."""
    if isinstance(entry, dict):
        return entry.get(attr, default)
    return getattr(entry, attr, default)


def _is_binary_entry(entry: object, norm: PurePosixPath) -> bool:
    """Determine whether entry represents a binary file."""
    is_bin = _get_entry_attr(entry, "is_binary", None)
    if is_bin is True:
        return True
    if is_bin is False:
        return False
    return norm.suffix.lower() in BINARY_EXTENSIONS


def _is_path_in_allowed_dirs(norm: PurePosixPath, allowed_dirs: Sequence[str] | set[str]) -> bool:
    """Return True if normalized path is within any of the allowed directories."""
    for allowed in allowed_dirs:
        norm_allowed = PurePosixPath(allowed.strip().replace("\\", "/"))
        if norm == norm_allowed or norm.is_relative_to(norm_allowed):
            return True
    return False


def _check_content_secrets(norm_path_str: str, content: str | None) -> list[Finding]:
    """Scan content for staged credential patterns."""
    findings: list[Finding] = []
    if not content:
        return findings
    for label, pattern in SECRET_CONTENT_PATTERNS:
        if pattern.search(content):
            findings.append(
                Finding(
                    category="secret",
                    message=f"Staged secret detected in {norm_path_str}: {label}",
                    path=norm_path_str,
                )
            )
    return findings


def _evaluate_mass_deletion(
    entries: list[object],
    tracked_count: int,
    thresholds: TripwireThresholds,
) -> list[Finding]:
    """Evaluate mass deletion thresholds."""
    findings: list[Finding] = []
    deleted_count = sum(1 for e in entries if _is_deleted(str(_get_entry_attr(e, "status", ""))))

    if thresholds.max_deleted_files >= 0 and deleted_count > thresholds.max_deleted_files:
        findings.append(
            Finding(
                category="mass_deletion",
                message=(
                    f"Mass deletion: {deleted_count} deleted file(s) exceeds "
                    f"limit of {thresholds.max_deleted_files}"
                ),
                count=deleted_count,
            )
        )

    if tracked_count > 0 and thresholds.max_deleted_fraction >= 0.0:
        deleted_fraction = deleted_count / tracked_count
        if deleted_fraction > thresholds.max_deleted_fraction:
            findings.append(
                Finding(
                    category="mass_deletion",
                    message=(
                        f"Mass deletion: {deleted_fraction:.1%} of tracked files deleted "
                        f"({deleted_count}/{tracked_count}) exceeds limit of "
                        f"{thresholds.max_deleted_fraction:.1%}"
                    ),
                    count=deleted_count,
                )
            )

    return findings


def _normalized_repo_path(raw_path: str) -> str:
    """Return a repository-relative POSIX path for a diff entry path."""
    return PurePosixPath(raw_path.replace("\\", "/")).as_posix()


def _collect_base_tree_paths(entries: list[object]) -> frozenset[str]:
    """Return the base-tree membership carried by the diff entries.

    Entries produced by the git layer carry the tracked paths of the tree the
    diff was computed against. Entries assembled without that membership
    contribute nothing, so a rule that needs it stays silent instead of
    guessing.
    """
    for entry in entries:
        paths = _get_entry_attr(entry, "base_tree_paths", None)
        if isinstance(paths, (set, frozenset, list, tuple)):
            return frozenset(str(path) for path in paths)
    return frozenset()


def _collect_deleted_paths(entries: list[object]) -> set[str]:
    """Return the normalized paths the diff deletes."""
    deleted: set[str] = set()
    for entry in entries:
        status = str(_get_entry_attr(entry, "status", ""))
        if not _is_deleted(status):
            continue
        deleted.add(_normalized_repo_path(str(_get_entry_attr(entry, "path", ""))))
    return deleted


def _evaluate_protected_directory_deletions(entries: list[object]) -> list[Finding]:
    """Fire when a protected top-level directory is deleted in its entirety.

    A recursive ``git diff-tree`` reports BLOB entries only: the removal of a
    whole directory arrives as one deleted blob path per tracked file and
    never as a directory entry. The base-tree membership is therefore the
    only evidence that the directory is gone as a whole. A PARTIAL deletion
    is not an entirety deletion and stays silent here; it remains subject to
    the mass-deletion thresholds.
    """
    base_paths = _collect_base_tree_paths(entries)
    if not base_paths:
        return []

    deleted_paths = _collect_deleted_paths(entries)
    findings: list[Finding] = []
    for directory in sorted(DEFAULT_TOP_LEVEL_SOURCE_TEST_DIRS):
        prefix = f"{directory}/"
        tracked_under = {path for path in base_paths if path.startswith(prefix)}
        if not tracked_under:
            continue
        if tracked_under <= deleted_paths:
            findings.append(
                Finding(
                    category="protected_deletion",
                    message=f"Top-level directory deleted in its entirety: {directory}",
                    path=directory,
                )
            )
    return findings


def _evaluate_hard_entry(
    norm: PurePosixPath,
    norm_str: str,
    deleted: bool,
    old_mode: object,
    custom_protected: set[str],
) -> Finding | None:
    """Evaluate hard protected tripwires for a single entry."""
    # Protected: .git content
    if _is_git_path(norm):
        action = "deleted" if deleted else "modified or overwritten"
        category = "protected_deletion" if deleted else "protected_overwrite"
        return Finding(
            category=category,
            message=f"Protected .git content {action}: {norm_str}",
            path=norm_str,
        )

    # Protected: canonical prompt and AGENTS.md
    if norm.name in DEFAULT_PROTECTED_DOCUMENTS:
        action = "deleted" if deleted else "overwritten"
        category = "protected_deletion" if deleted else "protected_overwrite"
        return Finding(
            category=category,
            message=f"Protected document {action}: {norm_str}",
            path=norm_str,
        )

    # Protected: custom protected paths
    if norm_str in custom_protected or any(fnmatchcase(norm_str, pat) for pat in custom_protected):
        action = "deleted" if deleted else "modified or overwritten"
        category = "protected_deletion" if deleted else "protected_overwrite"
        return Finding(
            category=category,
            message=f"Protected path {action}: {norm_str}",
            path=norm_str,
        )

    # Protected: manifest and lockfile removal
    is_manifest = norm.name in DEFAULT_LOCKFILE_BASENAMES or norm.name in DEFAULT_MANIFEST_BASENAMES
    if is_manifest and deleted:
        return Finding(
            category="protected_deletion",
            message=f"Protected project manifest or lockfile deleted: {norm_str}",
            path=norm_str,
        )

    # Protected: top-level source or test directory deletion
    is_top_level_dir = norm_str in DEFAULT_TOP_LEVEL_SOURCE_TEST_DIRS or (
        old_mode == "040000"
        and len(norm.parts) == 1
        and norm.parts[0] in DEFAULT_TOP_LEVEL_SOURCE_TEST_DIRS
    )
    if is_top_level_dir and deleted:
        return Finding(
            category="protected_deletion",
            message=f"Top-level directory deleted in its entirety: {norm_str}",
            path=norm_str,
        )

    return None


def _evaluate_soft_entry(
    entry: object,
    norm: PurePosixPath,
    norm_str: str,
    deleted: bool,
    size: int,
    content: object,
    allowed_dirs: Sequence[str] | set[str] | None,
    thresholds: TripwireThresholds,
) -> list[Finding]:
    """Evaluate soft evidence tripwires for a single entry."""
    findings: list[Finding] = []

    # Out-of-scope paths when allowed_directories is configured
    if allowed_dirs and not _is_path_in_allowed_dirs(norm, allowed_dirs):
        findings.append(
            Finding(
                category="out_of_scope",
                message=f"Path '{norm_str}' is outside allowed directories: {sorted(allowed_dirs)}",
                path=norm_str,
            )
        )

    # Staged secret file (not deleted)
    if not deleted and is_recognized_secret_path(norm_str):
        findings.append(
            Finding(
                category="secret",
                message=f"Recognized secret file staged: {norm_str}",
                path=norm_str,
            )
        )

    # Staged secret content scanning (not deleted)
    if not deleted and content:
        findings.extend(_check_content_secrets(norm_str, str(content)))

    # Oversized binary file (not deleted)
    if not deleted and _is_binary_entry(entry, norm) and size > thresholds.binary_size_limit:
        findings.append(
            Finding(
                category="binary_size",
                message=(
                    f"Binary file '{norm_str}' size ({size} bytes) "
                    f"exceeds limit ({thresholds.binary_size_limit} bytes)"
                ),
                path=norm_str,
                count=size,
            )
        )

    return findings


def evaluate_tripwires(
    diff_entries: Iterable[DiffEntryProtocol | object],
    tracked_count: int,
    thresholds: TripwireThresholds,
    allowed_dirs: Sequence[str] | set[str] | None = None,
    protected: Sequence[str] | set[str] | None = None,
) -> TripwireReport:
    """Evaluate diff entries against deterministic safety tripwires.

    Args:
        diff_entries: Iterable of diff entries (DiffEntry instances or duck-typed objects/dicts).
        tracked_count: Total number of tracked files in the repository before the diff.
        thresholds: TripwireThresholds instance configuring mass-deletion and binary limits.
        allowed_dirs: Optional collection of allowed directory prefixes for scope enforcement.
        protected: Optional collection of additional protected file paths/patterns.

    Returns:
        TripwireReport containing tuples of hard findings and soft findings.
    """
    entries = list(diff_entries)
    hard_findings = _evaluate_mass_deletion(entries, tracked_count, thresholds)
    hard_findings.extend(_evaluate_protected_directory_deletions(entries))
    soft_findings: list[Finding] = []

    custom_protected: set[str] = set()
    if protected is not None:
        custom_protected = {p.strip().replace("\\", "/") for p in protected}

    for entry in entries:
        raw_path = str(_get_entry_attr(entry, "path", ""))
        status = str(_get_entry_attr(entry, "status", ""))
        size = int(str(_get_entry_attr(entry, "size", 0) or 0))
        old_mode = _get_entry_attr(entry, "old_mode", None)
        content = _get_entry_attr(entry, "content", None)

        norm = PurePosixPath(raw_path.replace("\\", "/"))
        norm_str = norm.as_posix()
        deleted = _is_deleted(status)

        hard_finding = _evaluate_hard_entry(
            norm,
            norm_str,
            deleted,
            old_mode,
            custom_protected,
        )
        if hard_finding is not None:
            hard_findings.append(hard_finding)

        soft_findings.extend(
            _evaluate_soft_entry(
                entry,
                norm,
                norm_str,
                deleted,
                size,
                content,
                allowed_dirs,
                thresholds,
            )
        )

    return TripwireReport(hard=tuple(hard_findings), soft=tuple(soft_findings))


__all__ = [
    "BINARY_EXTENSIONS",
    "DEFAULT_BINARY_SIZE_LIMIT",
    "DEFAULT_LOCKFILE_BASENAMES",
    "DEFAULT_MANIFEST_BASENAMES",
    "DEFAULT_MAX_DELETED_FILES",
    "DEFAULT_MAX_DELETED_FRACTION",
    "DEFAULT_PROTECTED_DOCUMENTS",
    "DEFAULT_TOP_LEVEL_SOURCE_TEST_DIRS",
    "RECOGNIZED_SECRET_PATTERNS",
    "SECRET_CONTENT_PATTERNS",
    "DiffEntry",
    "DiffEntryProtocol",
    "Finding",
    "TripwireReport",
    "TripwireThresholds",
    "evaluate_tripwires",
    "is_recognized_secret_path",
]
