
from dataclasses import dataclass


@dataclass(frozen=True)
class CommitResidualEvent:

    committed_paths: tuple[str, ...]
    remaining_paths: tuple[str, ...]
    sha: str | None = None
