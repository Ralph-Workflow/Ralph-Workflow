from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EmptyCommitPhaseRole = Literal["commit", "commit_review"]


@dataclass(frozen=True)
class EmptyCommitEffect:
    phase_role: EmptyCommitPhaseRole = "commit"
