from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from ralph.skills._agent_paths import agent_skill_roots, canonical_agent_skill_root
from ralph.skills._content import _MANAGED_MARKER, BASELINE_SKILL_NAMES

if TYPE_CHECKING:
    from pathlib import Path

_PruneDecision = Literal["prune", "preserve", "unknown", "skip"]


_PRUNABLE_MANAGED_BY: frozenset[str] = frozenset({"ralph-workflow", "ralph-baseline"})

_UNIVERSAL_MANAGED_BY: str = "ralph-universal"

_LEGACY_MANAGED_FILE: str = "SKILL.md"


@dataclass(frozen=True)
class _PruneResult:
    pruned: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


def _managed_marker(skill_dir: Path) -> dict[str, object] | None:
    try:
        raw: object = json.loads((skill_dir / _MANAGED_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return cast("dict[str, object]", raw)


def _recorded_managed_files(marker: dict[str, object]) -> dict[str, str] | None:
    files = marker.get("files")
    if files is not None:
        if not isinstance(files, dict):
            return None
        recorded: dict[str, str] = {}
        for relpath, digest in files.items():
            if not isinstance(relpath, str) or not isinstance(digest, str):
                return None
            recorded[relpath] = digest
        return recorded
    digest = marker.get("installed_content_sha256")
    if isinstance(digest, str) and digest:
        return {_LEGACY_MANAGED_FILE: digest}
    return None


def _manifest_digest(recorded: dict[str, str]) -> str:
    manifest = dict(sorted(recorded.items()))
    payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _managed_content_matches(skill_dir: Path, recorded: dict[str, str]) -> bool:
    on_disk: set[str] = set()
    try:
        for path in skill_dir.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            relpath = path.relative_to(skill_dir).as_posix()
            if relpath == _MANAGED_MARKER:
                continue
            on_disk.add(relpath)
    except OSError:
        return False
    if on_disk != set(recorded):
        return False
    for relpath, digest in recorded.items():
        try:
            actual = hashlib.sha256((skill_dir / relpath).read_bytes()).hexdigest()
        except OSError:
            return False
        if actual != digest:
            return False
    return True


def _prune_decision(entry: Path) -> _PruneDecision:
    decision: _PruneDecision = "skip"
    if entry.is_dir() and (entry / _MANAGED_MARKER).exists():
        decision = _marked_entry_decision(entry)
    return decision


def _verified_recorded_files(marker: dict[str, object]) -> dict[str, str] | None:
    recorded = _recorded_managed_files(marker)
    if recorded is None:
        return None
    aggregate = marker.get("installed_sha256")
    if isinstance(aggregate, str) and aggregate and aggregate != _manifest_digest(recorded):
        return None
    return recorded


def _marked_entry_decision(entry: Path) -> _PruneDecision:
    marker = _managed_marker(entry)
    if marker is None:
        return "unknown"
    managed_by = marker.get("managed_by")
    if managed_by == _UNIVERSAL_MANAGED_BY:
        return "skip"
    if managed_by is not None and (
        not isinstance(managed_by, str) or managed_by not in _PRUNABLE_MANAGED_BY
    ):
        return "unknown"
    recorded = _verified_recorded_files(marker)
    if recorded is None:
        return "unknown"
    if not _managed_content_matches(entry, recorded):
        return "preserve"
    return "prune"


def _prune_removed_baseline_skills(root: Path) -> _PruneResult:
    pruned: list[str] = []
    preserved: list[str] = []
    unknown: list[str] = []
    if not root.is_dir() and not root.is_symlink():
        return _PruneResult()
    try:
        for entry in list(root.iterdir()):
            if entry.name in BASELINE_SKILL_NAMES:
                continue
            if entry.is_symlink():
                if not entry.exists():
                    try:
                        entry.unlink()
                    except OSError:
                        continue
                    pruned.append(entry.name)
                continue
            decision = _prune_decision(entry)
            if decision == "unknown":
                unknown.append(entry.name)
                continue
            if decision == "preserve":
                preserved.append(entry.name)
                continue
            if decision != "prune":
                continue
            try:
                shutil.rmtree(entry)
            except OSError:
                continue
            pruned.append(entry.name)
    except OSError:
        pass
    return _PruneResult(tuple(pruned), tuple(preserved), tuple(unknown))


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
    for root in ordered:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        failures.extend(_prune_failure_codes(_prune_removed_baseline_skills(resolved)))
    return failures


__all__ = [
    "_PruneResult",
    "_prune_failure_codes",
    "_prune_removed_baseline_skills",
    "_prune_user_global_skill_roots",
]
