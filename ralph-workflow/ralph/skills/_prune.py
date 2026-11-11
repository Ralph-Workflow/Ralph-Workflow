"""Source-aware, hash-aware prune of retired Ralph-managed skills.

A skill directory that the shipped bundle no longer contains is removed
ONLY when the directory proves it is Ralph-managed AND its complete
managed content still matches the digest its ``.ralph-managed.json``
marker recorded. Everything else is preserved: a user-owned directory, a
user-edited directory, a universal-marked directory, a malformed marker
and a managed directory carrying a user-added companion file all survive
the prune, and the ones the prune cannot prove pristine are REPORTED
through the installer's failure list.

The prune runs at EVERY registered scope root: the project canonical root,
every project sibling root and every registered user-global root. The
roots come from the registries in :mod:`ralph.skills._agent_paths`, so a
newly registered agent root is pruned without touching the caller.
"""

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

#: The prune decision for one skills-root entry.
_PruneDecision = Literal["prune", "preserve", "unknown", "skip"]


# --- Prune contract ------------------------------------------------------------
#
# A retired skill directory is removed ONLY when the directory proves it is
# Ralph-managed AND its content still matches the digest its marker recorded.
# A user-owned, user-edited, universal-marked, malformed or
# companion-file-carrying entry is ALWAYS preserved; the entries the prune
# cannot prove pristine are REPORTED through the installer's failure list.

#: ``managed_by`` marker values that mark a skill directory as Ralph-managed.
_PRUNABLE_MANAGED_BY: frozenset[str] = frozenset({"ralph-workflow", "ralph-baseline"})

#: ``managed_by`` marker value that marks a UNIVERSAL skill directory.
_UNIVERSAL_MANAGED_BY: str = "ralph-universal"

#: The only file a legacy Ralph marker implicitly owns.
_LEGACY_MANAGED_FILE: str = "SKILL.md"


@dataclass(frozen=True)
class _PruneResult:
    """Outcome of one prune pass over a single skills root.

    ``pruned`` lists the retired managed directories that were removed.
    ``preserved`` lists the managed directories whose content no longer
    matched the digest their marker recorded (a user edit or a user-added
    companion file). ``unknown`` lists the directories whose marker is
    malformed or records no usable digest. The latter two are REPORTED
    through the installer's failure list; neither is ever deleted.
    """

    pruned: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


def _managed_marker(skill_dir: Path) -> dict[str, object] | None:
    """Return the parsed managed marker of ``skill_dir``, or ``None``.

    ``None`` means the marker is UNUSABLE: unreadable, malformed JSON, or
    a JSON value that is not an object. Such a marker never authorises a
    delete; the prune preserves and reports the directory instead.
    """
    try:
        raw: object = json.loads((skill_dir / _MANAGED_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return cast("dict[str, object]", raw)


def _recorded_managed_files(marker: dict[str, object]) -> dict[str, str] | None:
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
        return {_LEGACY_MANAGED_FILE: digest}
    return None


def _manifest_digest(recorded: dict[str, str]) -> str:
    """Return the sha256 over the sorted file manifest.

    The manifest is serialized as compact JSON with sorted keys, so the
    digest depends only on the digests of the recorded files. This is the
    aggregate the new-style marker records under ``installed_sha256``.
    """
    manifest = dict(sorted(recorded.items()))
    payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _managed_content_matches(skill_dir: Path, recorded: dict[str, str]) -> bool:
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
    """Classify one skills-root entry for the prune.

    Returns ``"prune"`` when the entry is a retired managed directory whose
    complete content still matches the digest its marker recorded,
    ``"preserve"`` when it is a managed directory the prune cannot prove
    pristine (a user edit or a user-added companion file), ``"unknown"``
    when its marker is malformed or records no usable digest, and
    ``"skip"`` for every entry the prune never touches.
    """
    decision: _PruneDecision = "skip"
    if entry.is_dir() and (entry / _MANAGED_MARKER).exists():
        decision = _marked_entry_decision(entry)
    return decision


def _verified_recorded_files(marker: dict[str, object]) -> dict[str, str] | None:
    """Return the marker's recorded file map when it is internally consistent.

    ``None`` means the marker records no usable digest, or its aggregate
    digest (``installed_sha256``) disagrees with its own file map: an
    UNKNOWN marker shape that never authorises a delete.
    """
    recorded = _recorded_managed_files(marker)
    if recorded is None:
        return None
    aggregate = marker.get("installed_sha256")
    if isinstance(aggregate, str) and aggregate and aggregate != _manifest_digest(recorded):
        return None
    return recorded


def _marked_entry_decision(entry: Path) -> _PruneDecision:
    """Classify a skills-root entry that carries a managed marker."""
    marker = _managed_marker(entry)
    if marker is None:
        return "unknown"
    managed_by = marker.get("managed_by")
    if managed_by == _UNIVERSAL_MANAGED_BY:
        return "skip"  # universal skill: never pruned
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
    """Remove retired Ralph-managed skill directories from one skills root.

    A directory is removed ONLY when ALL of the following hold:

    1. it carries a ``.ralph-managed.json`` marker that parses as a JSON
       object (a directory WITHOUT a marker is user-owned and untouched);
    2. the marker's ``managed_by`` is absent, ``ralph-workflow`` or
       ``ralph-baseline`` -- a ``ralph-universal`` marker is NEVER pruned;
    3. its name is not in ``BASELINE_SKILL_NAMES`` (the skill is retired);
    4. its complete managed content matches the digest the marker recorded
       -- a hash mismatch, a missing digest, an unknown marker shape or a
       user-added companion file preserves the directory and reports it.

    Broken symlinks whose name is no longer in the baseline are unlinked
    (they point at a canonical entry that no longer exists), while a
    baseline symlink -- and a LIVE symlink to a retired canonical entry --
    is left intact.

    Args:
        root: A skills root (project canonical, project sibling or
            user-global root).

    Returns:
        The prune outcome: the pruned names plus the preserved and unknown
        names the caller has to report.

    The helper is fail-closed: any ``OSError`` during the walk or the
    delete is swallowed and the prune continues (best-effort) so the
    surrounding install can still finish cleanly even if one entry's
    permissions are wrong.
    """
    pruned: list[str] = []
    preserved: list[str] = []
    unknown: list[str] = []
    if not root.is_dir() and not root.is_symlink():
        return _PruneResult()
    try:
        for entry in list(root.iterdir()):
            if entry.name in BASELINE_SKILL_NAMES:
                continue
            # A broken symlink fails both ``is_dir()`` and ``marker.exists()``,
            # so the managed-entry heuristic below would skip it. The name
            # is no longer in the baseline, so the link is dead and the
            # sibling fan-out will never recreate it; unlink unconditionally.
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
                # filesystem-write-ok: bounded pruning of a retired managed skill whose content still matches its recorded digest
                shutil.rmtree(entry)
            except OSError:
                continue
            pruned.append(entry.name)
    except OSError:
        pass
    return _PruneResult(tuple(pruned), tuple(preserved), tuple(unknown))


def _prune_failure_codes(result: _PruneResult) -> list[str]:
    """Return the failure codes that report a prune's preserved entries."""
    codes = [f"skills-prune-preserved-{name}" for name in result.preserved]
    codes.extend(f"skills-prune-unknown-{name}" for name in result.unknown)
    return codes


def _prune_user_global_skill_roots() -> list[str]:
    """Prune every REGISTERED user-global skills root and report the outcome.

    The roots come from the ``agent_skill_roots()`` registry, so a newly
    registered agent root is pruned without touching this module. The
    canonical root is pruned FIRST: a sibling root holds a symlink to the
    canonical entry, so removing the canonical entry first turns that
    sibling symlink into a broken link that the same pass unlinks. Two
    roots that resolve to the SAME directory (a sibling registered as a
    symlink to the canonical) are pruned once.

    Returns:
        The failure codes for the entries the prune preserved.
    """
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
