from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.skills._agent_paths import agent_skill_roots, canonical_agent_skill_root
from ralph.skills._content import BASELINE_SKILL_NAMES
from ralph.skills._ownership import Ownership, classify_entry

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class _PruneResult:
    pruned: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    removed_paths: tuple[Path, ...] = ()


def _resolved_path(entry: Path) -> Path | None:
    try:
        return entry.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _prune_managed_directories(entries: list[Path]) -> _PruneResult:
    pruned: list[str] = []
    preserved: list[str] = []
    unknown: list[str] = []
    removed_paths: list[Path] = []
    for entry in entries:
        if entry.is_symlink():
            continue
        ownership = classify_entry(entry)
        if ownership is Ownership.MANAGED:
            removed = _resolved_path(entry)
            try:
                shutil.rmtree(entry)
            except OSError:
                continue
            pruned.append(entry.name)
            if removed is not None:
                removed_paths.append(removed)
        elif ownership is Ownership.USER_EDITED:
            preserved.append(entry.name)
        elif ownership is Ownership.UNKNOWN:
            unknown.append(entry.name)
    return _PruneResult(tuple(pruned), tuple(preserved), tuple(unknown), tuple(removed_paths))


def _prune_symlink_entries(entries: list[Path], attributable: frozenset[Path]) -> _PruneResult:
    pruned: list[str] = []
    unknown: list[str] = []
    for entry in entries:
        if not entry.is_symlink():
            continue
        if entry.exists():
            continue
        target = _resolved_path(entry)
        if target is not None and target in attributable:
            try:
                entry.unlink()
            except OSError:
                continue
            pruned.append(entry.name)
            continue
        unknown.append(entry.name)
    return _PruneResult(pruned=tuple(pruned), unknown=tuple(unknown))


def _prune_removed_baseline_skills(
    root: Path,
    *,
    removed_targets: frozenset[Path] = frozenset(),
) -> _PruneResult:
    if not root.is_dir() and not root.is_symlink():
        return _PruneResult()
    try:
        entries = [entry for entry in root.iterdir() if entry.name not in BASELINE_SKILL_NAMES]
        directories = _prune_managed_directories(entries)
        links = _prune_symlink_entries(entries, removed_targets.union(directories.removed_paths))
    except OSError:
        return _PruneResult()
    return _PruneResult(
        pruned=(*directories.pruned, *links.pruned),
        preserved=directories.preserved,
        unknown=(*directories.unknown, *links.unknown),
        removed_paths=directories.removed_paths,
    )


def _prune_failure_codes(result: _PruneResult) -> list[str]:
    codes = [f"skills-prune-preserved-{name}" for name in result.preserved]
    codes.extend(f"skills-prune-unknown-{name}" for name in result.unknown)
    return codes


def _prune_user_global_skill_roots() -> list[str]:
    canonical = canonical_agent_skill_root()
    ordered = [
        canonical,
        *(root for root in agent_skill_roots() if root.agent != canonical.agent),
    ]
    failures: list[str] = []
    seen: set[Path] = set()
    removed: set[Path] = set()
    for root in ordered:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result = _prune_removed_baseline_skills(resolved, removed_targets=frozenset(removed))
        removed.update(result.removed_paths)
        failures.extend(_prune_failure_codes(result))
    return failures


__all__ = [
    "_PruneResult",
    "_prune_failure_codes",
    "_prune_removed_baseline_skills",
    "_prune_user_global_skill_roots",
]
