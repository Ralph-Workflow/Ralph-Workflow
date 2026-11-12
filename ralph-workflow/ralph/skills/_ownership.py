"""Ownership classification for skill entries.

ONE shared predicate module: every delete / replace / relink of a skill
entry is gated by the verdict produced here, so Ralph removes or replaces
ONLY the entries it can prove it manages. The prune and the project-scope
installer both import this module -- there is exactly ONE implementation of
the marker-shape rules, not two copies.

The verdict is derived from the managed marker (``.ralph-managed.json``)
that sits next to the installed skill content:

* a directory WITHOUT a marker is user-owned (Ralph never installed it);
* a marker that does not parse, or that records no usable digest, is an
  UNKNOWN marker shape -- it never authorises a delete;
* a ``ralph-universal`` marker (or any other owner that is not Ralph) marks
  the directory as FOREIGN -- it belongs to another component;
* a Ralph marker whose recorded digests no longer match the on-disk content
  (a user edit, or a user-added companion file) marks the directory as
  USER-EDITED;
* a Ralph marker whose recorded digests still match the complete on-disk
  content marks the directory as MANAGED -- the ONLY verdict that authorises
  a delete or a replace.

A symlink is classified by the directory it resolves to: a live link to a
MANAGED directory is MANAGED, a live link to anything else is USER-OWNED,
and a broken link is UNKNOWN (Ralph cannot prove what it points at).
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

#: The managed-marker file name every Ralph-managed skill directory carries.
MANAGED_MARKER_NAME: str = ".ralph-managed.json"

#: ``managed_by`` marker values that mark a skill directory as Ralph-managed.
RALPH_MANAGED_BY: frozenset[str] = frozenset({"ralph-workflow", "ralph-baseline"})

#: ``managed_by`` marker value that marks a UNIVERSAL skill directory.
UNIVERSAL_MANAGED_BY: str = "ralph-universal"

#: The only file a legacy Ralph marker implicitly owns.
LEGACY_MANAGED_FILE: str = "SKILL.md"


class Ownership(StrEnum):
    """What Ralph can prove about one skill entry.

    ``MANAGED`` is the only verdict that authorises a delete or a replace;
    every other verdict means the entry is preserved.

    The enum is a ``StrEnum`` (the repo-wide convention): every member is
    a ``str`` whose value is the wire form (``"managed"``, ``"unknown"``,
    ...), so ``Ownership.MANAGED == "managed"`` holds.
    """

    ABSENT = "absent"
    MANAGED = "managed"
    USER_EDITED = "user-edited"
    USER_OWNED = "user-owned"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


def read_managed_marker(skill_dir: Path) -> dict[str, object] | None:
    """Return the parsed managed marker of ``skill_dir``, or ``None``.

    ``None`` means the marker is UNUSABLE: unreadable, malformed JSON, or
    a JSON value that is not an object. Such a marker never authorises a
    delete; the entry is preserved and reported instead.
    """
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
    """Return the marker's recorded managed-file map, or ``None``.

    A new-style marker records the managed file set explicitly under
    ``files`` (relative path -> sha256). A legacy marker records only the
    primary content digest under ``installed_content_sha256`` and therefore
    owns exactly one file (``SKILL.md``). A marker with neither shape is an
    UNKNOWN marker shape: it never authorises a delete.
    """
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
    """Return the marker's recorded file map when it is internally consistent.

    ``None`` means the marker records no usable digest, or its aggregate
    digest (``installed_sha256``) disagrees with its own file map: an
    UNKNOWN marker shape that never authorises a delete.
    """
    recorded = recorded_managed_files(marker)
    if recorded is None:
        return None
    aggregate = marker.get("installed_sha256")
    if isinstance(aggregate, str) and aggregate and aggregate != manifest_digest(recorded):
        return None
    return recorded


def manifest_digest(recorded: dict[str, str]) -> str:
    """Return the sha256 over the sorted file manifest.

    The manifest is serialized as compact JSON with sorted keys, so the
    digest depends only on the digests of the recorded files. This is the
    aggregate the new-style marker records under ``installed_sha256``.
    """
    manifest = dict(sorted(recorded.items()))
    payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def managed_content_matches(skill_dir: Path, recorded: dict[str, str]) -> bool:
    """Return True when the on-disk content matches the recorded digests.

    Every recorded file must exist with its recorded digest AND the
    directory must carry NO additional file: a user-added companion file
    means the directory is no longer purely Ralph-managed.
    """
    on_disk: set[str] = set()
    try:
        for path in skill_dir.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            relpath = path.relative_to(skill_dir).as_posix()
            if relpath == MANAGED_MARKER_NAME:
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
    """Classify one skills-root entry by the ownership Ralph can prove.

    A symlink is classified by the directory it resolves to; every other
    entry is classified by its own marker and on-disk content.

    Args:
        entry: The skills-root entry to classify.

    Returns:
        The ownership verdict. Only :attr:`Ownership.MANAGED` authorises a
        delete or a replace.
    """
    if entry.is_symlink():
        return _classify_symlink(entry)
    return _classify_path(entry)


def may_replace(entry: Path) -> bool:
    """Return True when Ralph may overwrite ``entry`` with bundled content."""
    return classify_entry(entry) is Ownership.MANAGED


def may_delete(entry: Path) -> bool:
    """Return True when Ralph may delete ``entry``."""
    return classify_entry(entry) is Ownership.MANAGED


def _classify_symlink(entry: Path) -> Ownership:
    """Classify a symlink by the directory it resolves to.

    A live link to a MANAGED directory is MANAGED; a live link to anything
    else is USER-OWNED (Ralph did not install the target); a broken link is
    UNKNOWN because Ralph cannot prove what it points at.
    """
    try:
        resolved = entry.resolve(strict=True)
    except (OSError, RuntimeError):
        return Ownership.UNKNOWN
    if _classify_path(resolved) is Ownership.MANAGED:
        return Ownership.MANAGED
    return Ownership.USER_OWNED


def _classify_path(path: Path) -> Ownership:
    """Classify a directory by its managed marker and its on-disk content."""
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
    """Classify a directory that carries a (parsable) managed marker."""
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
    """Return True when the marker's ``managed_by`` names Ralph as the owner.

    An ABSENT ``managed_by`` counts as Ralph's (the legacy marker shape);
    every other value must name Ralph explicitly.
    """
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
