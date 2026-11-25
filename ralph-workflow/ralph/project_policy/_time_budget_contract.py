from __future__ import annotations

import re
from typing import Final

from ralph.project_policy import markers
from ralph.project_policy._findings import PolicyFinding

TIME_BUDGET_POLICY_FILES: Final[frozenset[str]] = frozenset(
    {"verification-policy.md", "testing-policy.md"}
)

_FIXED_CAP_RE: Final[re.Pattern[str]] = re.compile(
    r"hard cap:? (?:(?:30|60) seconds|2 minutes)(?! above)"
    r"|startup alone exceeding the target is a defect to fix",
    re.IGNORECASE,
)

_REQUIRED_OUTCOME: Final[str] = (
    "replace every fixed universal time cap with the bundled starter's measured "
    "cost-floor contract (verification-policy.md rules 9, 16 and 21; "
    "testing-policy.md rule 9): caps bind only the time above the project's "
    "measured cost floor, and the policy records `RALPH-FACT: "
    "verification_cost_floor` and `RALPH-FACT: fast_path_time_budget` from "
    "measurement, never from a cap"
)


def _normalized(content: str) -> str:
    return " ".join(content.replace("*", "").split())


def check_fixed_time_caps(content: str, path: str, filename: str) -> list[PolicyFinding]:
    if filename not in TIME_BUDGET_POLICY_FILES:
        return []
    matches = [m.group(0) for m in _FIXED_CAP_RE.finditer(_normalized(content))]
    if not matches:
        return []
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_TIME_BUDGET}:{filename}:fixed-cap",
            path=path,
            missing_evidence=(
                "fixed universal time cap wording that ignores the project's measured "
                f"cost floor: {'; '.join(sorted(set(matches)))}"
            ),
            required_outcome=_REQUIRED_OUTCOME,
        )
    ]


__all__ = ["TIME_BUDGET_POLICY_FILES", "check_fixed_time_caps"]
