from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ralph.config.gitlink_policy_config import GitlinkPolicyConfig, GitlinkPreference


@dataclass(frozen=True, slots=True)
class GitlinkReviewRecord:
    path: str
    policy: GitlinkPreference
    base: str
    ours: str
    theirs: str
    selected_side: str
    selected_sha: str
    alternatives_considered: tuple[str, ...]


POLICY_CANDIDATES: Final[dict[GitlinkPreference, tuple[str, ...]]] = {
    GitlinkPreference.OURS: ("ours", "theirs"),
    GitlinkPreference.THEIRS: ("theirs", "ours"),
    GitlinkPreference.VENDOR_IMMUTABLE: ("unchanged-from-base", "ours", "theirs"),
}


def choose_gitlink(
    path: str,
    base: str,
    ours: str,
    theirs: str,
    config: GitlinkPolicyConfig | None = None,
) -> GitlinkReviewRecord:
    policy = (config or GitlinkPolicyConfig()).pointer_preference
    alternatives = POLICY_CANDIDATES[policy]
    values = {"ours": ours, "theirs": theirs}
    selected_side = "ours"
    for candidate in alternatives:
        if candidate == "unchanged-from-base":
            unchanged = tuple(side for side, sha in values.items() if sha == base)
            if len(unchanged) == 1:
                selected_side = unchanged[0]
                break
            continue
        selected_side = candidate
        break
    return GitlinkReviewRecord(
        path=path,
        policy=policy,
        base=base,
        ours=ours,
        theirs=theirs,
        selected_side=selected_side,
        selected_sha=values[selected_side],
        alternatives_considered=alternatives,
    )


__all__ = ["POLICY_CANDIDATES", "GitlinkReviewRecord", "choose_gitlink"]
