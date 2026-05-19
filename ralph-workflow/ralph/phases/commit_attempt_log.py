"""CommitAttemptLog: per-attempt log for commit message generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CommitAttemptLog:
    """Per-attempt log for commit message generation."""

    attempt_number: int
    agent: str
    strategy: str
    timestamp: datetime = field(default_factory=datetime.now)
    prompt_size_bytes: int = 0
    diff_size_bytes: int = 0
    diff_was_truncated: bool = False
    raw_output: str | None = None
    outcome: str | None = None

    def with_prompt_size(self, size: int) -> CommitAttemptLog:
        self.prompt_size_bytes = size
        return self

    def with_diff_info(self, size: int, was_truncated: bool) -> CommitAttemptLog:
        self.diff_size_bytes = size
        self.diff_was_truncated = was_truncated
        return self

    def with_raw_output(self, output: str) -> CommitAttemptLog:
        const_max_output_size = 50000
        if len(output) > const_max_output_size:
            half = const_max_output_size // 2
            self.raw_output = (
                f"{output[:half]}\n\n"
                f"[... truncated {len(output) - const_max_output_size} bytes ...]\n\n"
                f"{output[len(output) - half :]}"
            )
        else:
            self.raw_output = output
        return self

    def with_outcome(self, outcome: str) -> CommitAttemptLog:
        self.outcome = outcome
        return self
