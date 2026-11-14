from __future__ import annotations

from dataclasses import dataclass

__all__ = ["IntegrationWorkShape"]


@dataclass(frozen=True, slots=True)
class IntegrationWorkShape:
    commit_count: int = 0
    many_commits: bool = False
    contains_merge_commits: bool = False
    history_already_published: bool = False
    conflicts_expected: bool = False
    conflicts_seen: bool = False
    hot_conflict_repetition: int = 0
