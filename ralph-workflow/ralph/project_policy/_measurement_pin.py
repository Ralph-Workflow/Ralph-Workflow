from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.project_policy import markers
from ralph.project_policy._volatile_evidence import (
    measurement_fact_key,
    refreshed_measurement_facts,
    refreshed_measurement_lines,
)

if TYPE_CHECKING:
    from pathlib import Path


def _git_text(repo_root: Path, args: tuple[str, ...], label: str) -> str | None:
    result = run_git(args, cwd=repo_root, label=label)
    if result.returncode != 0:
        return None
    return result.stdout


def _head_fact_lines(head_text: str) -> dict[str, str]:
    lines: dict[str, str] = {}
    for line in head_text.splitlines():
        key = measurement_fact_key(line)
        if key is not None and key not in lines:
            lines[key] = line
    return lines


def _pin_file(repo_root: Path, rel_path: str, keys: set[str]) -> bool:
    head_text = _git_text(repo_root, ("show", f"HEAD:{rel_path}"), "policy:pin-measurement-head")
    if head_text is None:
        return False
    head_lines = _head_fact_lines(head_text)
    path = repo_root / rel_path
    current = path.read_text(encoding="utf-8")
    changed = False
    out: list[str] = []
    for line in current.splitlines(keepends=True):
        key = measurement_fact_key(line)
        if key in keys and key in head_lines and line.rstrip("\n") != head_lines[key]:
            newline = "\n" if line.endswith("\n") else ""
            out.append(head_lines[key] + newline)
            changed = True
        else:
            out.append(line)
    if changed:
        write_text_if_changed(DEFAULT_FILE_BACKEND, path, "".join(out), encoding="utf-8")
    return changed


def _pin_lines(repo_root: Path, rel_path: str, pairs: list[tuple[str, str]]) -> int:
    path = repo_root / rel_path
    current = path.read_text(encoding="utf-8")
    restored = 0
    lines = current.splitlines(keepends=True)
    for old, new in pairs:
        for index, line in enumerate(lines):
            if line.rstrip("\n") == new:
                lines[index] = old + ("\n" if line.endswith("\n") else "")
                restored += 1
                break
    if restored:
        write_text_if_changed(DEFAULT_FILE_BACKEND, path, "".join(lines), encoding="utf-8")
    return restored


def pin_measurement_facts(repo_root: Path) -> list[str]:
    diff = _git_text(
        repo_root,
        ("diff", "-U0", "--no-color", "--no-ext-diff", "HEAD", "--", markers.CANONICAL_DIR),
        "policy:pin-measurement-diff",
    )
    if not diff:
        return []
    refreshed = refreshed_measurement_facts(diff)
    by_file: dict[str, set[str]] = {}
    for entry in refreshed:
        rel_path, _sep, key = entry.partition(": ")
        by_file.setdefault(rel_path, set()).add(key)
    pinned: list[str] = []
    for rel_path, keys in sorted(by_file.items()):
        try:
            if _pin_file(repo_root, rel_path, keys):
                pinned.extend(f"{rel_path}: {key}" for key in sorted(keys))
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("measurement pin skipped {}: {}", rel_path, exc)
    line_pairs: dict[str, list[tuple[str, str]]] = {}
    for rel_path, old_line, new_line in refreshed_measurement_lines(diff):
        line_pairs.setdefault(rel_path, []).append((old_line, new_line))
    for rel_path, pairs in sorted(line_pairs.items()):
        try:
            restored = _pin_lines(repo_root, rel_path, pairs)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("measurement pin skipped {}: {}", rel_path, exc)
            continue
        pinned.extend(f"{rel_path}: re-recorded run timing" for _ in range(restored))
    if pinned:
        logger.info(
            "restored {} measured-figure fact(s) a development commit re-recorded: {}",
            len(pinned),
            ", ".join(pinned),
        )
    return pinned


__all__ = ["pin_measurement_facts"]
