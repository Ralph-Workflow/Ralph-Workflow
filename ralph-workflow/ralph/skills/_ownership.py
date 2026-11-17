from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

MANAGED_MARKER_NAME: str = ".ralph-managed.json"

RALPH_MANAGED_BY: frozenset[str] = frozenset({"ralph-workflow", "ralph-baseline"})

UNIVERSAL_MANAGED_BY: str = "ralph-universal"

LEGACY_MANAGED_FILE: str = "SKILL.md"


class Ownership(StrEnum):
    ABSENT = "absent"
    MANAGED = "managed"
    USER_EDITED = "user-edited"
    USER_OWNED = "user-owned"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


def read_managed_marker(skill_dir: Path) -> dict[str, object] | None:
    try:
        raw: object = json.loads(
            (skill_dir / MANAGED_MARKER_NAME).read_text(encoding="utf-8"),
        )
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return cast("dict[str, object]", raw)


def recorded_managed_files(marker: dict[str, object]) -> dict[str, str] | None:
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
        return {LEGACY_MANAGED_FILE: digest}
    return None


def verified_recorded_files(marker: dict[str, object]) -> dict[str, str] | None:
    recorded = recorded_managed_files(marker)
    if recorded is None:
        return None
    aggregate = marker.get("installed_sha256")
    if isinstance(aggregate, str) and aggregate and aggregate != manifest_digest(recorded):
        return None
    return recorded


def manifest_digest(recorded: dict[str, str]) -> str:
    manifest = dict(sorted(recorded.items()))
    payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def managed_content_matches(skill_dir: Path, recorded: dict[str, str]) -> bool:
    on_disk: set[str] = set()
    try:
        for path in skill_dir.rglob("*"):
            relpath = path.relative_to(skill_dir).as_posix()
            if relpath == MANAGED_MARKER_NAME:
                continue
            if path.is_symlink():
                if relpath not in recorded:
                    return False
                on_disk.add(relpath)
                continue
            if not path.is_file():
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


def classify_entry(entry: Path) -> Ownership:
    if entry.is_symlink():
        return _classify_symlink(entry)
    return _classify_path(entry)


def may_replace(entry: Path) -> bool:
    return classify_entry(entry) is Ownership.MANAGED


def may_delete(entry: Path) -> bool:
    return classify_entry(entry) is Ownership.MANAGED


def _classify_symlink(entry: Path) -> Ownership:
    try:
        resolved = entry.resolve(strict=True)
    except (OSError, RuntimeError):
        return Ownership.UNKNOWN
    if _classify_path(resolved) is Ownership.MANAGED:
        return Ownership.MANAGED
    return Ownership.USER_OWNED


def _classify_path(path: Path) -> Ownership:
    if not path.exists():
        return Ownership.ABSENT
    if not path.is_dir():
        return Ownership.USER_OWNED
    if not (path / MANAGED_MARKER_NAME).exists():
        return Ownership.USER_OWNED
    marker = read_managed_marker(path)
    if marker is None:
        return Ownership.UNKNOWN
    return _classify_marked_path(path, marker)


def _classify_marked_path(path: Path, marker: dict[str, object]) -> Ownership:
    managed_by = marker.get("managed_by")
    if managed_by == UNIVERSAL_MANAGED_BY:
        return Ownership.FOREIGN
    recorded = verified_recorded_files(marker)
    if recorded is None:
        return Ownership.UNKNOWN
    if not _is_ralph_owner(managed_by):
        return Ownership.FOREIGN
    if not managed_content_matches(path, recorded):
        return Ownership.USER_EDITED
    return Ownership.MANAGED


def _is_ralph_owner(managed_by: object) -> bool:
    if managed_by is None:
        return True
    return isinstance(managed_by, str) and managed_by in RALPH_MANAGED_BY


__all__ = [
    "LEGACY_MANAGED_FILE",
    "MANAGED_MARKER_NAME",
    "RALPH_MANAGED_BY",
    "UNIVERSAL_MANAGED_BY",
    "Ownership",
    "classify_entry",
    "managed_content_matches",
    "manifest_digest",
    "may_delete",
    "may_replace",
    "read_managed_marker",
    "recorded_managed_files",
    "verified_recorded_files",
]
