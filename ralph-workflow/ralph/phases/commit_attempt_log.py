from __future__ import annotations

from datetime import datetime


class CommitAttemptLog:
    __slots__ = (
        "agent",
        "attempt_number",
        "diff_size_bytes",
        "diff_was_truncated",
        "outcome",
        "prompt_size_bytes",
        "raw_output",
        "strategy",
        "timestamp",
    )

    def __init__(
        self,
        attempt_number: int,
        agent: str,
        strategy: str,
        timestamp: datetime | None = None,
        prompt_size_bytes: int = 0,
        diff_size_bytes: int = 0,
        diff_was_truncated: bool = False,
        raw_output: str | None = None,
        outcome: str | None = None,
    ) -> None:
        self.attempt_number = attempt_number
        self.agent = agent
        self.strategy = strategy
        self.timestamp = datetime.now() if timestamp is None else timestamp
        self.prompt_size_bytes = prompt_size_bytes
        self.diff_size_bytes = diff_size_bytes
        self.diff_was_truncated = diff_was_truncated
        self.raw_output = raw_output
        self.outcome = outcome

    def with_prompt_size(self, size: int) -> CommitAttemptLog:
        self.prompt_size_bytes = size
        return self

    def with_diff_info(self, size: int, *, was_truncated: bool) -> CommitAttemptLog:
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
