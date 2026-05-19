"""Directory listing, glob matching, and recursive walk operations."""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ralph.mcp.tools.workspace._utils import join_path, list_dir_entries, normalize_relative_path
from ralph.workspace.skip import RECURSIVE_SKIP_DIRECTORY_NAMES

if TYPE_CHECKING:
    from ralph.workspace import Workspace


def list_dir_flat(workspace: Workspace, path: str) -> str:
    normalized = normalize_relative_path(path)
    entries = list_dir_entries(workspace, normalized)
    output = f"Directory: {path}\n"
    for entry in sorted(entries):
        entry_path = join_path(normalized, entry)
        entry_type = "[DIR]" if workspace.is_dir(entry_path) else "[FILE]"
        output += f"  {entry_type} {entry_path}\n"
    return output


def _should_recurse_into_directory(workspace: Workspace, entry_path: str) -> bool:
    entry_name = PurePosixPath(entry_path).name
    if entry_name in RECURSIVE_SKIP_DIRECTORY_NAMES:
        return False
    return not workspace.exists(join_path(entry_path, ".git"))


def _append_dir_entry(workspace: Workspace, entry_path: str, output: list[str], depth: int) -> None:
    indent = "  " * depth
    is_dir = workspace.is_dir(entry_path)
    entry_type = "[DIR]" if is_dir else "[FILE]"
    output.append(f"{indent}{entry_type} {entry_path}\n")
    if is_dir and _should_recurse_into_directory(workspace, entry_path):
        _walk_directory_recursive(workspace, entry_path, output, depth + 1)


def _walk_directory_recursive(
    workspace: Workspace,
    path: str,
    output: list[str],
    depth: int,
) -> None:
    entries = list_dir_entries(workspace, path)
    for entry in sorted(entries):
        entry_path = join_path(path, entry)
        _append_dir_entry(workspace, entry_path, output, depth)


def _list_dir_recursive_output(workspace: Workspace, path: str) -> str:
    normalized = normalize_relative_path(path)
    output_lines: list[str] = [f"Directory (recursive): {path}\n"]
    _walk_directory_recursive(workspace, normalized, output_lines, 0)
    return "".join(output_lines)


def _match_parts_with_doublestar(path_parts: list[str], pat_parts: list[str]) -> bool:
    """Recursively match path segments against a pattern with ** segments."""
    if not pat_parts:
        return not path_parts
    if pat_parts[0] == "**":
        remaining = pat_parts[1:]
        if not remaining:
            return True
        for i in range(len(path_parts) + 1):
            if _match_parts_with_doublestar(path_parts[i:], remaining):
                return True
        return False
    if not path_parts:
        return False
    return fnmatch.fnmatchcase(path_parts[0], pat_parts[0]) and _match_parts_with_doublestar(
        path_parts[1:], pat_parts[1:]
    )


def match_glob(rel_path: str, pattern: str) -> bool:
    """Match a path against a glob pattern supporting *, **, and ? segments."""
    path_parts = rel_path.split("/")
    pat_parts = pattern.split("/")
    if "**" in pat_parts:
        return _match_parts_with_doublestar(path_parts, pat_parts)
    if len(pat_parts) == 1:
        return any(fnmatch.fnmatchcase(seg, pattern) for seg in path_parts)
    if len(path_parts) < len(pat_parts):
        return False
    tail = path_parts[-len(pat_parts) :]
    return all(fnmatch.fnmatchcase(p, q) for p, q in zip(tail, pat_parts, strict=False))


def _collect_files_recursive(workspace: Workspace, base_path: str) -> list[str]:
    """Recursively collect all files under base_path, respecting skip dirs."""
    results: list[str] = []
    entries = list_dir_entries(workspace, base_path)
    for entry in sorted(entries):
        entry_path = join_path(base_path, entry)
        if workspace.is_dir(entry_path):
            if _should_recurse_into_directory(workspace, entry_path):
                results.extend(_collect_files_recursive(workspace, entry_path))
        elif workspace.is_file(entry_path):
            results.append(entry_path)
    return results


def _collect_matching_files(
    workspace: Workspace,
    base_path: str,
    pattern: str,
    exclude: list[str] | None = None,
) -> list[str]:
    """Collect files matching a glob pattern under base_path."""
    try:
        all_files: list[str] = list(workspace.iter_files(base_path))
    except Exception:
        all_files = _collect_files_recursive(workspace, base_path)

    matches: list[str] = []
    for file_path in all_files:
        if not match_glob(file_path, pattern):
            continue
        if exclude and any(match_glob(file_path, ex) for ex in exclude):
            continue
        matches.append(file_path)

    return sorted(matches)
