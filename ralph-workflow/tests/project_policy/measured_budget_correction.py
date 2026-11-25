from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.project_policy import markers

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace

_FIXED_CAP_RE = re.compile(r"(HARD CAP:?\s+(?:30\s+seconds|60\s+seconds|2\s+minutes))")
_STARTUP_DEFECT_RE = re.compile(
    r"Startup\s+alone\s+exceeding\s+the\s+target\s+is\s+a\s+defect\s+to\s+fix"
)

LEGACY_TIME_BUDGET_FINDING_IDS: tuple[str, ...] = (
    f"{markers.ID_TIME_BUDGET}:testing-policy.md:fixed-cap",
    f"{markers.ID_PLACEHOLDER}:verification-policy.md:fact-fast_path_time_budget",
    f"{markers.ID_PLACEHOLDER}:verification-policy.md:fact-verification_cost_floor",
    f"{markers.ID_TIME_BUDGET}:verification-policy.md:fixed-cap",
)


def correct_fixed_time_caps(workspace: Workspace) -> None:
    for filename in ("verification-policy.md", "testing-policy.md"):
        path = f"{markers.CANONICAL_DIR}{filename}"
        content = _FIXED_CAP_RE.sub(r"\1 above the recorded cost floor", workspace.read(path))
        content = _STARTUP_DEFECT_RE.sub(
            "Startup above the target is recorded as the cost floor and reduced as owned work",
            content,
        )
        if filename == "verification-policy.md":
            content += (
                "\nRALPH-FACT: verification_cost_floor: verified-value"
                "\nRALPH-FACT: fast_path_time_budget: verified-value\n"
            )
        workspace.write(path, content)


__all__ = ["LEGACY_TIME_BUDGET_FINDING_IDS", "correct_fixed_time_caps"]
