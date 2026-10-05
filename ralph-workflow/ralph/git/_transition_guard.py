"""Transition-staging conflict guard for ``ralph.git.scoped_auto_commit``.

Extracted to keep ``scoped_auto_commit.py`` under the 1000-line
repo-structure cap. The guard enforces the wt-012 DA-003/DA-008
isolation boundary: an ancestor transition stage (``git add --all --
<ancestor>`` after a dir→symlink replacement) sweeps the deletion of
EVERY tracked descendant -- including descendants the commit helper
just SKIPPED as pre-write dirty. A git tree cannot hold the
replacement symlink and the kept descendant side by side, so the only
safe move is to drop the conflicting ancestor from the commit.
"""

from __future__ import annotations

from loguru import logger


def drop_transition_conflicts(stageable: list[str], skipped: list[str]) -> list[str]:
    """Move stageable entries whose staging would sweep a SKIPPED descendant.

    ``git add --all -- <ancestor>`` stages the deletion of EVERY tracked
    descendant together with the ancestor entry. When a descendant was
    SKIPPED as pre-write dirty (HEAD != pre-write hash), committing the
    ancestor silently commits that skipped deletion -- the wt-012
    DA-003/DA-008 counterexample, where a dir→symlink transition
    reported ``skipped_paths=('old/wip',)`` yet the commit still
    recorded ``D old/wip``. A git tree cannot hold the replacement
    symlink and the kept descendant side by side, so the only safe move
    is to drop the conflicting ancestor -- and every stageable entry
    beneath it, which can only be staged through that ancestor -- from
    the commit and report the dropped paths alongside the skipped ones.
    """
    conflicting = [
        entry
        for entry in stageable
        if any(k == entry or k.startswith(f"{entry}/") for k in skipped)
    ]
    if not conflicting:
        return stageable
    kept: list[str] = []
    for entry in stageable:
        if entry in conflicting or any(entry.startswith(f"{c}/") for c in conflicting):
            if entry not in skipped:
                skipped.append(entry)
            logger.warning(
                "commit_deterministic_writes: path {} requires ancestor transition "
                "staging that would sweep a pre-write-dirty skipped descendant; "
                "skipping to keep the chore commit isolated",
                entry,
            )
        else:
            kept.append(entry)
    return kept
