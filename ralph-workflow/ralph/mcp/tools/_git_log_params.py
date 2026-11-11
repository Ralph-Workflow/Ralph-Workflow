
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GitLogFormat = Literal["raw", "summary"]


@dataclass(frozen=True)
class GitLogParams:

    count: int
    format: GitLogFormat = "raw"
