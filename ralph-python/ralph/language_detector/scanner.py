"""Workspace scanning utilities for language detection."""

from __future__ import annotations

from collections import deque
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.workspace.protocol import Workspace

MAX_FILES_TO_SCAN = 2000
MAX_SIGNATURE_SEARCH_DEPTH = 6
SKIP_DIR_NAMES: set[str] = {
    "node_modules",
    "target",
    "dist",
    "build",
    "vendor",
    "__pycache__",
    "venv",
    ".venv",
    "env",
}
SIGNATURE_FILE_NAMES: set[str] = {
    "cargo.toml",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "pipfile",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "bun.lock",
    "gemfile",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
}
TEST_DIRECTORY_NAMES: set[str] = {"tests", "test", "spec", "__tests__"}


def normalize_path(path: str) -> str:
    normalized = str(PurePosixPath(path))
    return "" if normalized in {"", "."} else normalized


def join_path(parent: str, child: str) -> str:
    if not parent:
        return normalize_path(child)
    return normalize_path(str(PurePosixPath(parent) / child))


def should_skip_dir_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(".") or lowered in SKIP_DIR_NAMES


def iter_files(workspace: Workspace, root: str = "") -> Iterator[str]:
    queue: deque[str] = deque([normalize_path(root)])
    visited: set[str] = set()
    scanned = 0

    while queue and scanned < MAX_FILES_TO_SCAN:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        try:
            entries = workspace.list_dir(current)
        except FileNotFoundError:
            continue

        for entry in entries:
            child_path = join_path(current, entry)
            if workspace.is_dir(child_path):
                if not should_skip_dir_name(entry):
                    queue.append(child_path)
                continue
            if workspace.is_file(child_path):
                scanned += 1
                yield child_path
                if scanned >= MAX_FILES_TO_SCAN:
                    break


def count_extensions(workspace: Workspace, root: str = "") -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in iter_files(workspace, root):
        suffix = PurePosixPath(path).suffix
        if not suffix:
            continue
        extension = suffix[1:].lower()
        counts[extension] = counts.get(extension, 0) + 1
    return counts


def collect_signature_files(workspace: Workspace, root: str = "") -> dict[str, list[str]]:
    signatures: dict[str, list[str]] = {}
    queue: deque[tuple[str, int]] = deque([(normalize_path(root), 0)])

    while queue:
        current, depth = queue.popleft()
        try:
            entries = workspace.list_dir(current)
        except FileNotFoundError:
            continue

        for entry in entries:
            entry_lower = entry.lower()
            child_path = join_path(current, entry)
            if workspace.is_dir(child_path):
                if depth < MAX_SIGNATURE_SEARCH_DEPTH and not should_skip_dir_name(entry_lower):
                    queue.append((child_path, depth + 1))
                continue
            if workspace.is_file(child_path) and entry_lower in SIGNATURE_FILE_NAMES:
                signatures.setdefault(entry_lower, []).append(child_path)

    return signatures


def is_test_file_name(file_name: str, primary_language: str, path_components: list[str]) -> bool:
    lower_name = file_name.lower()
    language_checks = {
        "Go": lambda: lower_name.endswith("_test.go"),
        "PHP": lambda: lower_name.endswith("test.php") or lower_name.endswith("spec.php"),
        "Python": lambda: (
            (lower_name.startswith("test_") and lower_name.endswith(".py"))
            or lower_name.endswith("_test.py")
        ),
        "Ruby": lambda: lower_name.endswith("_spec.rb") or lower_name.endswith("_test.rb"),
    }
    if primary_language == "Rust":
        return (
            lower_name == "tests.rs"
            or lower_name.endswith("_test.rs")
            or (lower_name.endswith(".rs") and "tests" in path_components)
        )
    if primary_language in {"JavaScript", "TypeScript"}:
        return any(
            lower_name.endswith(suffix)
            for suffix in (
                ".test.js",
                ".spec.js",
                ".test.ts",
                ".spec.ts",
                ".test.tsx",
                ".spec.tsx",
            )
        )
    if primary_language == "Java":
        return ("src" in path_components and "test" in path_components) or lower_name.endswith(
            "test.java"
        )
    check = language_checks.get(primary_language)
    if check is not None:
        return check()
    return "test" in lower_name or "spec" in lower_name


def detect_tests(workspace: Workspace, root: str = "", primary_language: str = "Unknown") -> bool:
    queue: deque[str] = deque([normalize_path(root)])
    scanned = 0

    while queue and scanned < MAX_FILES_TO_SCAN:
        current = queue.popleft()
        try:
            entries = workspace.list_dir(current)
        except FileNotFoundError:
            continue

        for entry in entries:
            entry_path = join_path(current, entry)
            entry_lower = entry.lower()
            if workspace.is_dir(entry_path):
                if entry_lower in TEST_DIRECTORY_NAMES:
                    return True
                if should_skip_dir_name(entry_lower):
                    continue
                queue.append(entry_path)
                continue
            if workspace.is_file(entry_path):
                scanned += 1
                components = [part.lower() for part in PurePosixPath(entry_path).parts]
                if is_test_file_name(entry, primary_language, components):
                    return True
                if scanned >= MAX_FILES_TO_SCAN:
                    break

    return False
