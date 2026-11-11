
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GitShowFormat = Literal["raw", "summary"]


@dataclass(frozen=True)
class GitShowParams:

    git_ref: str
    format: GitShowFormat = "raw"
