"""Shared content-set helpers for the dependency and secret-scan gates.

Both gates take their input as a *content set*: a mapping of
repository-relative path to file text. The CALLER builds that set (the
coordinator wires a git-diff based baseline); the gates themselves never
shell out, never read version control, and never touch the network. That
seam is what makes the gates deterministic and unit-testable with purely
synthetic input.

Two readers are provided:

``read_tree``
    Every readable text file under a root, keyed by POSIX relative path.
    Used by the secret-scan / SAST gate, which scans source, config and
    documentation files alike.

``read_manifests``
    Only the named manifest files (e.g. ``pyproject.toml``, ``uv.lock``)
    that exist under a root. Used by the dependency-integrity gate, whose
    scope is the package-manager manifest and its lockfile.

Both readers skip generated / vendored directories and files that are not
decodable as UTF-8, so a binary blob in the tree can never turn into a
spurious finding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: Directories never scanned: caches, virtualenvs, vendored trees and
#: build output. Mirrors the skip policy of the sibling audits.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "venv",
    }
)

#: Files larger than this are skipped: a multi-megabyte blob is a build
#: artifact, not a source file, and scanning it costs budget for nothing.
MAX_FILE_BYTES: int = 2 * 1024 * 1024


def _is_skipped(relative: Path) -> bool:
    """Return True when *relative* sits inside a skipped directory."""
    return any(part in SKIP_DIRS for part in relative.parts)


def read_tree(root: Path) -> dict[str, str]:
    """Return ``{relative posix path: text}`` for every text file under *root*.

    Unreadable, oversized and non-UTF-8 files are skipped silently: they
    carry no reviewable content, and a decode error must never turn into a
    gate failure with no actionable finding.
    """
    contents: dict[str, str] = {}
    if not root.is_dir():
        return contents
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _is_skipped(relative):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        contents[relative.as_posix()] = text
    return contents


def read_manifests(root: Path, names: frozenset[str]) -> dict[str, str]:
    """Return ``{name: text}`` for the manifest files in *names* present under *root*.

    Only the top level of *root* is inspected: ``pyproject.toml`` and
    ``uv.lock`` are package-root manifests, and a nested copy of the same
    name inside a fixture directory is not the project's manifest.
    """
    manifests: dict[str, str] = {}
    for name in sorted(names):
        path = root / name
        if not path.is_file():
            continue
        try:
            manifests[name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return manifests
