
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommitPolicyExplanation:

    increments_counter: str | None
    loop_resets: list[str]
    requires_artifact: bool
