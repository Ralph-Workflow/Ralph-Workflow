from __future__ import annotations

from enum import StrEnum

__all__ = ["StrategyTransitionGuard"]


class StrategyTransitionGuard(StrEnum):
    SHARED_HISTORY_DISCOVERED = "shared_history_discovered"
    REPEATED_HOT_CONFLICT = "repeated_hot_conflict"
    REPLAY_RANGE_SHIFTED = "replay_range_shifted"
    NEW_FEATURE_WORK = "new_feature_work"
    NEW_TARGET_WORK = "new_target_work"
    FAST_FORWARD_DISALLOWED_BY_POLICY = "fast_forward_disallowed_by_policy"
    REBASE_DISALLOWED_BY_POLICY = "rebase_disallowed_by_policy"
    MERGE_DISALLOWED_BY_POLICY = "merge_disallowed_by_policy"
