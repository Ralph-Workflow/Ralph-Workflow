"""Source-aware, hash-aware prune of retired Ralph-managed skills.

A skill directory that the shipped bundle no longer contains is removed
ONLY when the directory proves it is Ralph-managed AND its complete
managed content still matches the digest its ``.ralph-managed.json``
marker recorded. Everything else is preserved: a user-owned directory, a
user-edited directory, a universal-marked directory, a malformed marker
and a managed directory carrying a user-added companion file all survive
the prune, and the ones the prune cannot prove pristine are REPORTED
through the installer's failure list.

The ownership rules live in ONE shared module
(:mod:`ralph.skills._ownership`), so the prune and the project-scope
installer gate every delete / replace / relink on the SAME predicate.

The prune runs at EVERY registered scope root: the project canonical root,
every project sibling root and every registered user-global root. The
roots come from the registries in :mod:`ralph.skills._agent_paths`, so a
newly registered agent root is pruned without touching the caller.

A broken symlink is unlinked ONLY when the prune can attribute its missing
target to a Ralph-managed canonical entry the SAME prune pass just removed.
A broken link Ralph cannot attribute is preserved and reported instead.
"""

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
    """Outcome of one prune pass over a single skills root.

    ``pruned`` lists the retired managed entries that were removed.
    ``preserved`` lists the managed entries whose content no longer
    matched the digest their marker recorded (a user edit or a user-added
    companion file). ``unknown`` lists the entries whose marker is
    malformed, records no usable digest, or whose broken symlink Ralph
    cannot attribute to a just-pruned managed entry. The latter two are
    REPORTED through the installer's failure list; neither is ever
    deleted.

    ``removed_paths`` lists the resolved paths the pass removed, so the
    caller can attribute a broken sibling symlink to the canonical entry
    the SAME pass just deleted.
    """

    pruned: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    removed_paths: tuple[Path, ...] = ()


def _resolved_path(entry: Path) -> Path | None:
    """Return the resolved path of ``entry``, or ``None`` when unresolvable."""
    try:
        return entry.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _prune_managed_directories(entries: list[Path]) -> _PruneResult:
    """Remove the retired managed DIRECTORIES among ``entries``.

    A directory is removed ONLY when the shared ownership predicate proves
    Ralph manages it AND its complete content still matches the digest its
    marker recorded. A user-edited directory and a directory whose marker
    is unusable are preserved; the latter two are reported.
    """
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
                # filesystem-write-ok: bounded pruning of a retired managed skill whose content still matches its recorded digest
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
        # USER_OWNED, FOREIGN and ABSENT entries are preserved silently:
        # Ralph did not install them, so they are not its to report.
    return _PruneResult(tuple(pruned), tuple(preserved), tuple(unknown), tuple(removed_paths))


def _prune_symlink_entries(entries: list[Path], attributable: frozenset[Path]) -> _PruneResult:
    """Unlink the broken symlinks Ralph can attribute to a removed entry.

    A broken link is unlinked ONLY when its missing target is one of the
    ``attributable`` paths -- the Ralph-managed entries the same install
    just removed. Every other broken link is preserved and reported; a LIVE
    link is left intact.
    """
    pruned: list[str] = []
    unknown: list[str] = []
    for entry in entries:
        if not entry.is_symlink():
            continue
        if entry.exists():
            continue  # a live link points at a directory that still exists
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

    A broken symlink whose name is no longer in the baseline is unlinked
    ONLY when its target is a Ralph-managed canonical entry the SAME prune
    pass just removed -- either a directory this pass deleted or one of the
    ``removed_targets`` the caller threaded in from the canonical root's
    prune. A broken link Ralph cannot attribute (and a LIVE symlink to a
    retired canonical entry) is preserved; the unattributable one is
    reported.

    Args:
        root: A skills root (project canonical, project sibling or
            user-global root).
        removed_targets: The resolved paths of the Ralph-managed entries a
            PREVIOUS prune pass of the same install just removed. A broken
            symlink is unlinked only when its target is in this set.

    Returns:
        The prune outcome: the pruned names plus the preserved and unknown
        names the caller has to report, and the resolved paths the pass
        removed.

    The helper is fail-closed: any ``OSError`` during the walk or the
    delete is swallowed and the prune continues (best-effort) so the
    surrounding install can still finish cleanly even if one entry's
    permissions are wrong.
    """
    if not root.is_dir() and not root.is_symlink():
        return _PruneResult()
    try:
        entries = [entry for entry in root.iterdir() if entry.name not in BASELINE_SKILL_NAMES]
        # First pass: the managed DIRECTORIES. A retired managed directory
        # is removed here, and the resolved path it occupied is recorded
        # so a symlink that pointed at it becomes attributable.
        directories = _prune_managed_directories(entries)
        # Second pass: the SYMLINKS. A link whose target the first pass
        # removed is broken AND attributable, so it is unlinked; a link
        # Ralph cannot attribute is preserved and reported.
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
    """Prune every REGISTERED user-global skills root and report the outcome.

    The roots come from the ``agent_skill_roots()`` registry, so a newly
    registered agent root is pruned without touching this module. The
    canonical root is pruned FIRST: a sibling root holds a symlink to the
    canonical entry, so removing the canonical entry first turns that
    sibling symlink into a broken link -- which the SAME pass then unlinks
    because it can attribute the missing target to the canonical entry it
    just removed. Two roots that resolve to the SAME directory (a sibling
    registered as a symlink to the canonical) are pruned once.

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
