
from __future__ import annotations

from loguru import logger


def drop_transition_conflicts(stageable: list[str], skipped: list[str]) -> list[str]:
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
