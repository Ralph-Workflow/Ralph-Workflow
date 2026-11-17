from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard, runtime_checkable

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

SOURCE_CODE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".asm",
        ".astro",
        ".bash",
        ".c",
        ".cc",
        ".clj",
        ".cljs",
        ".cljc",
        ".coffee",
        ".cpp",
        ".cr",
        ".cs",
        ".cxx",
        ".dart",
        ".def",
        ".edn",
        ".elm",
        ".erl",
        ".ex",
        ".exs",
        ".f03",
        ".f90",
        ".f95",
        ".fish",
        ".fs",
        ".fsx",
        ".go",
        ".gleam",
        ".groovy",
        ".h",
        ".hh",
        ".hpp",
        ".hrl",
        ".hs",
        ".hxx",
        ".inc",
        ".java",
        ".jl",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".less",
        ".lhs",
        ".lua",
        ".m",
        ".mjs",
        ".ml",
        ".mli",
        ".mm",
        ".mts",
        ".nim",
        ".pas",
        ".php",
        ".pl",
        ".pm",
        ".pp",
        ".proto",
        ".ps1",
        ".psm1",
        ".py",
        ".pyx",
        ".pyi",
        ".r",
        ".rb",
        ".rs",
        ".s",
        ".sass",
        ".scala",
        ".scss",
        ".sh",
        ".sql",
        ".sv",
        ".swift",
        ".svelte",
        ".ts",
        ".tsx",
        ".v",
        ".vb",
        ".vue",
        ".zsh",
    }
)

TEST_CODE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".approved",
        ".expected",
        ".feature",
        ".snap",
        ".stories",
    }
)

TEST_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "test",
        "tests",
    }
)

_MIN_PATH_PARTS_IN_A_DIRECTORY: Final[int] = 2

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
    max_deleted_files: int = DEFAULT_MAX_DELETED_FILES
    max_deleted_fraction: float = DEFAULT_MAX_DELETED_FRACTION
    binary_size_limit: int = DEFAULT_BINARY_SIZE_LIMIT


@dataclass(frozen=True)
class _DiffEntry:
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
    s = status.strip().upper()
    return s == "D" or s.startswith("D") or s == "DELETED"


def _is_git_path(norm: PurePosixPath) -> bool:
    parts = norm.parts
    return len(parts) > 0 and parts[0] == ".git"


def is_recognized_secret_path(path: str) -> bool:
    norm = PurePosixPath(path.replace("\\", "/"))
    return any(fnmatchcase(norm.name, pat) for pat in RECOGNIZED_SECRET_PATTERNS)


def _get_entry_attr(entry: object, attr: str, default: object = None) -> object:
    if isinstance(entry, dict):
        return entry.get(attr, default)
    return getattr(entry, attr, default)


def _is_binary_entry(entry: object, norm: PurePosixPath) -> bool:
    is_bin = _get_entry_attr(entry, "is_binary", None)
    if is_bin is True:
        return True
    if is_bin is False:
        return False
    return norm.suffix.lower() in BINARY_EXTENSIONS


def _is_path_in_allowed_dirs(norm: PurePosixPath, allowed_dirs: Sequence[str] | set[str]) -> bool:
    for allowed in allowed_dirs:
        norm_allowed = PurePosixPath(allowed.strip().replace("\\", "/"))
        if norm == norm_allowed or norm.is_relative_to(norm_allowed):
            return True
    return False


def _check_content_secrets(norm_path_str: str, content: str | None) -> list[Finding]:
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


def _resolve_fraction_denominator(entries: list[object], tracked_count: int) -> int:
    membership = _collect_base_tree_paths(entries)
    if membership is not None:
        return len(membership)
    return tracked_count


def _evaluate_mass_deletion(
    entries: list[object],
    tracked_count: int,
    thresholds: TripwireThresholds,
    approved_deletions: frozenset[str],
) -> list[Finding]:
    findings: list[Finding] = []
    deleted_count = len(_collect_deleted_paths(entries) - approved_deletions)

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

    denominator = _resolve_fraction_denominator(entries, tracked_count)
    if denominator > 0 and thresholds.max_deleted_fraction >= 0.0:
        deleted_fraction = deleted_count / denominator
        if deleted_fraction > thresholds.max_deleted_fraction:
            findings.append(
                Finding(
                    category="mass_deletion",
                    message=(
                        f"Mass deletion: {deleted_fraction:.1%} of tracked files deleted "
                        f"({deleted_count}/{denominator}) exceeds limit of "
                        f"{thresholds.max_deleted_fraction:.1%}"
                    ),
                    count=deleted_count,
                )
            )

    return findings


def _normalized_repo_path(raw_path: str) -> str:
    return PurePosixPath(raw_path.replace("\\", "/")).as_posix()


def _is_path_collection(
    value: object,
) -> TypeGuard[list[object] | tuple[object, ...] | set[object] | frozenset[object]]:
    match value:
        case list() | tuple() | set() | frozenset():
            return True
        case _:
            return False


def _collect_base_tree_paths(entries: list[object]) -> frozenset[str] | None:
    for entry in entries:
        paths = _get_entry_attr(entry, "base_tree_paths", None)
        if isinstance(paths, (set, frozenset, list, tuple)):
            return frozenset(str(path) for path in paths)
    return None


def resolve_immutable_tracked_count(entries: Iterable[object]) -> int | None:
    materialized = list(entries)
    membership = _collect_base_tree_paths(materialized)
    if membership is not None:
        return len(membership)
    proposes_deletions = any(
        _is_deleted(str(_get_entry_attr(entry, "status", ""))) for entry in materialized
    )
    if proposes_deletions:
        return None
    return 0


def _collect_deleted_paths(entries: list[object]) -> set[str]:
    deleted: set[str] = set()
    for entry in entries:
        status = str(_get_entry_attr(entry, "status", ""))
        if not _is_deleted(status):
            continue
        deleted.add(_normalized_repo_path(str(_get_entry_attr(entry, "path", ""))))
    return deleted


def _path_suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    return name[dot:].lower()


def _source_test_top_level_dirs(base_paths: Iterable[str]) -> set[str]:
    directories: set[str] = set()
    for path in base_paths:
        parts = path.split("/")
        if len(parts) < _MIN_PATH_PARTS_IN_A_DIRECTORY or not parts[0]:
            continue
        top = parts[0]
        if top in directories:
            continue
        suffix = _path_suffix(path)
        if suffix in SOURCE_CODE_EXTENSIONS or suffix in TEST_CODE_EXTENSIONS:
            directories.add(top)
            continue
        if any(part.lower() in TEST_DIRECTORY_NAMES for part in parts[1:-1]):
            directories.add(top)
    return directories


def _protected_top_level_dirs(base_paths: frozenset[str] | None) -> frozenset[str]:
    if not base_paths:
        return frozenset(DEFAULT_TOP_LEVEL_SOURCE_TEST_DIRS)
    return frozenset(DEFAULT_TOP_LEVEL_SOURCE_TEST_DIRS | _source_test_top_level_dirs(base_paths))


def _evaluate_protected_directory_deletions(
    base_paths: frozenset[str] | None,
    deleted_paths: set[str],
    protected_dirs: frozenset[str],
) -> list[Finding]:
    if not base_paths:
        return []

    findings: list[Finding] = []
    for directory in sorted(protected_dirs):
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


def _is_destructive_manifest_change(
    content: object,
    new_mode: object,
    *,
    is_binary: bool,
) -> bool:
    return bool(
        is_binary or new_mode == "120000" or (content is not None and not str(content).strip())
    )


def _evaluate_manifest_hard_entry(
    norm: PurePosixPath,
    norm_str: str,
    content: object,
    new_mode: object,
    *,
    deleted: bool,
    is_binary: bool,
) -> Finding | None:
    if norm.name not in DEFAULT_LOCKFILE_BASENAMES and norm.name not in DEFAULT_MANIFEST_BASENAMES:
        return None
    if deleted:
        return Finding(
            category="protected_deletion",
            message=f"Protected project manifest or lockfile deleted: {norm_str}",
            path=norm_str,
        )
    if _is_destructive_manifest_change(content, new_mode, is_binary=is_binary):
        return Finding(
            category="protected_overwrite",
            message=f"Protected project manifest or lockfile overwritten destructively: {norm_str}",
            path=norm_str,
        )
    return None


def _evaluate_hard_entry(
    norm: PurePosixPath,
    norm_str: str,
    old_mode: object,
    protected_top_level_dirs: frozenset[str],
    *,
    deleted: bool,
    content: object = None,
    new_mode: object = None,
    is_binary: bool = False,
) -> Finding | None:
    if _is_git_path(norm):
        action = "deleted" if deleted else "modified or overwritten"
        category = "protected_deletion" if deleted else "protected_overwrite"
        return Finding(
            category=category,
            message=f"Protected .git content {action}: {norm_str}",
            path=norm_str,
        )

    if norm.name in DEFAULT_PROTECTED_DOCUMENTS:
        action = "deleted" if deleted else "overwritten"
        category = "protected_deletion" if deleted else "protected_overwrite"
        return Finding(
            category=category,
            message=f"Protected document {action}: {norm_str}",
            path=norm_str,
        )

    manifest_finding = _evaluate_manifest_hard_entry(
        norm, norm_str, content, new_mode, deleted=deleted, is_binary=is_binary
    )
    if manifest_finding is not None:
        return manifest_finding

    is_top_level_dir = norm_str in protected_top_level_dirs or (
        old_mode == "040000" and len(norm.parts) == 1 and norm.parts[0] in protected_top_level_dirs
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
    size: int,
    new_mode: object,
    content: object,
    allowed_dirs: Sequence[str] | set[str] | None,
    thresholds: TripwireThresholds,
    base_paths: frozenset[str] | None,
    *,
    deleted: bool,
    is_binary: bool,
) -> list[Finding]:
    findings: list[Finding] = []

    is_manifest = norm.name in DEFAULT_LOCKFILE_BASENAMES or norm.name in DEFAULT_MANIFEST_BASENAMES
    is_bin = _is_binary_entry(entry, norm)
    new_mode = _get_entry_attr(entry, "new_mode", None)
    if (
        not deleted
        and is_manifest
        and not _is_destructive_manifest_change(content, new_mode, is_binary=is_bin)
    ):
        findings.append(
            Finding(
                category="protected_overwrite",
                message=f"Protected project manifest or lockfile modified: {norm_str}",
                path=norm_str,
            )
        )

    if allowed_dirs and not _is_path_in_allowed_dirs(norm, allowed_dirs):
        findings.append(
            Finding(
                category="out_of_scope",
                message=f"Path '{norm_str}' is outside allowed directories: {sorted(allowed_dirs)}",
                path=norm_str,
            )
        )

    if not deleted and is_recognized_secret_path(norm_str):
        findings.append(
            Finding(
                category="secret",
                message=f"Recognized secret file staged: {norm_str}",
                path=norm_str,
            )
        )

    if not deleted and content:
        findings.extend(_check_content_secrets(norm_str, str(content)))

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
    approved_deletions: frozenset[str] | set[str] | None = None,
) -> TripwireReport:
    entries = list(diff_entries)
    base_paths = _collect_base_tree_paths(entries)
    deleted_paths = _collect_deleted_paths(entries)
    protected_dirs = _protected_top_level_dirs(base_paths)

    normalized_approved = frozenset(
        _normalized_repo_path(path) for path in (approved_deletions or ())
    )
    hard_findings = _evaluate_mass_deletion(
        entries,
        tracked_count,
        thresholds,
        normalized_approved,
    )
    hard_findings.extend(
        _evaluate_protected_directory_deletions(base_paths, deleted_paths, protected_dirs)
    )
    soft_findings: list[Finding] = []

    for entry in entries:
        raw_path = str(_get_entry_attr(entry, "path", ""))
        status = str(_get_entry_attr(entry, "status", ""))
        size = int(str(_get_entry_attr(entry, "size", 0) or 0))
        old_mode = _get_entry_attr(entry, "old_mode", None)
        new_mode = _get_entry_attr(entry, "new_mode", None)
        content = _get_entry_attr(entry, "content", None)
        is_binary = bool(_get_entry_attr(entry, "is_binary", default=False))

        norm = PurePosixPath(raw_path.replace("\\", "/"))
        norm_str = norm.as_posix()
        deleted = _is_deleted(status)
        is_binary = _is_binary_entry(entry, norm)

        hard_finding = _evaluate_hard_entry(
            norm,
            norm_str,
            old_mode,
            protected_dirs,
            deleted=deleted,
            content=content,
            new_mode=new_mode,
            is_binary=is_binary,
        )
        if hard_finding is not None:
            hard_findings.append(hard_finding)

        soft_findings.extend(
            _evaluate_soft_entry(
                entry,
                norm,
                norm_str,
                size,
                new_mode,
                content,
                allowed_dirs,
                thresholds,
                base_paths,
                deleted=deleted,
                is_binary=is_binary,
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
    "SOURCE_CODE_EXTENSIONS",
    "TEST_CODE_EXTENSIONS",
    "TEST_DIRECTORY_NAMES",
    "DiffEntry",
    "DiffEntryProtocol",
    "Finding",
    "TripwireReport",
    "TripwireThresholds",
    "evaluate_tripwires",
    "is_recognized_secret_path",
    "resolve_immutable_tracked_count",
]
