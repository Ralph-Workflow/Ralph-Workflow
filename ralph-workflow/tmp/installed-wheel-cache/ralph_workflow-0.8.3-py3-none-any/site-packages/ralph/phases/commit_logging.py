"""Commit logging session for tracking commit generation attempts.

Ported from ralph-workflow/src/phases/commit_logging/io.rs.

This module provides detailed logging for each commit generation attempt,
creating a clear audit trail for debugging parsing failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Maximum agent name length for filename sanitization
MAX_AGENT_NAME_LENGTH = 20


@dataclass
class CommitAttemptLog:
    """Per-attempt log for commit message generation.

    Captures all details about a single attempt to generate a commit message.

    Attributes:
        attempt_number: Attempt number within this session.
        agent: Agent being used (e.g., "claude", "glm").
        strategy: Retry strategy (e.g., "initial", "strict_json").
        timestamp: Timestamp when attempt started.
        prompt_size_bytes: Size of the prompt in bytes.
        diff_size_bytes: Size of the diff in bytes.
        diff_was_truncated: Whether the diff was pre-truncated.
        raw_output: Raw output from the agent.
        outcome: Final outcome of this attempt.
    """

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
        """Set the prompt size.

        Args:
            size: Prompt size in bytes.

        Returns:
            A copy with the prompt size set.
        """
        self.prompt_size_bytes = size
        return self

    def with_diff_info(self, size: int, was_truncated: bool) -> CommitAttemptLog:
        """Set the diff information.

        Args:
            size: Diff size in bytes.
            was_truncated: Whether the diff was truncated.

        Returns:
            A copy with the diff info set.
        """
        self.diff_size_bytes = size
        self.diff_was_truncated = was_truncated
        return self

    def with_raw_output(self, output: str) -> CommitAttemptLog:
        """Set the raw output from the agent.

        Truncates very large outputs to prevent log file bloat.

        Args:
            output: Raw output string.

        Returns:
            A copy with the raw output set (truncated if too large).
        """
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
        """Set the final outcome.

        Args:
            outcome: Outcome string.

        Returns:
            A copy with the outcome set.
        """
        self.outcome = outcome
        return self


@dataclass
class CommitLoggingSession:
    """Session tracker for commit generation logging.

    Manages a unique run directory for a commit generation session,
    ensuring log files are organized and don't overwrite each other.

    Attributes:
        run_dir: Base log directory path.
        attempt_counter: Current attempt counter.
        is_noop: Whether this is a no-op session.
    """

    run_dir: Path
    attempt_counter: int = 0
    is_noop: bool = False

    @classmethod
    def new(
        cls,
        base_log_dir: str,
        workspace_exists_func: Callable[[Path], bool],
        workspace_makedirs_func: Callable[[Path], None],
    ) -> CommitLoggingSession:
        """Create a new logging session.

        Creates a unique run directory under the base log path.

        Args:
            base_log_dir: Base directory for logs.
            workspace_exists_func: Function to check if path exists (workspace.exists).
            workspace_makedirs_func: Function to create directories (workspace.create_dir_all).

        Returns:
            A new CommitLoggingSession instance.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = Path(base_log_dir) / f"run_{timestamp}"

        if not workspace_exists_func(run_dir):
            workspace_makedirs_func(run_dir)

        return cls(run_dir=run_dir, attempt_counter=0, is_noop=False)

    @classmethod
    def noop(cls) -> CommitLoggingSession:
        """Create a no-op logging session that discards all writes.

        Returns:
            A no-op CommitLoggingSession instance.
        """
        return cls(
            run_dir=Path("/dev/null/ralph-noop-session"),
            attempt_counter=0,
            is_noop=True,
        )

    def next_attempt_number(self) -> int:
        """Get the next attempt number and increment the counter.

        Returns:
            The next attempt number.
        """
        self.attempt_counter = self.attempt_counter + 1
        return self.attempt_counter

    def new_attempt(self, agent: str, strategy: str) -> CommitAttemptLog:
        """Create a new attempt log.

        Args:
            agent: Agent name.
            strategy: Retry strategy.

        Returns:
            A new CommitAttemptLog instance.
        """
        attempt_number = self.next_attempt_number()
        return CommitAttemptLog(
            attempt_number=attempt_number,
            agent=agent,
            strategy=strategy,
        )

    def write_summary(
        self,
        total_attempts: int,
        final_outcome: str,
        workspace_write_func: Callable[[str, str], None],
    ) -> None:
        """Write summary file at end of session.

        For no-op sessions, this silently succeeds without writing anything.

        Args:
            total_attempts: Total number of attempts.
            final_outcome: Final outcome string.
            workspace_write_func: Function to write to workspace (workspace.write).
        """
        if self.is_noop:
            return

        summary_path = self.run_dir / "SUMMARY.txt"

        content = (
            f"COMMIT GENERATION SESSION SUMMARY\n"
            f"================================\n"
            f"\n"
            f"Run directory: {self.run_dir}\n"
            f"Total attempts: {total_attempts}\n"
            f"Final outcome: {final_outcome}\n"
            f"\n"
            f"Individual attempt logs are in this directory.\n"
        )

        workspace_write_func(str(summary_path), content)

    def write_attempt_log(
        self,
        attempt_log: CommitAttemptLog,
        workspace_write_func: Callable[[str, str], None],
    ) -> None:
        """Write an attempt log to a file.

        Args:
            attempt_log: The attempt log to write.
            workspace_write_func: Function to write to workspace (workspace.write).
        """
        if self.is_noop:
            return

        sanitized_agent = _sanitize_agent_name(attempt_log.agent)
        filename = (
            f"attempt_{attempt_log.attempt_number:03d}_"
            f"{sanitized_agent}_"
            f"{attempt_log.strategy.replace(' ', '_')}_"
            f"{attempt_log.timestamp.strftime('%Y%m%dT%H%M%S')}.log"
        )
        log_path = self.run_dir / filename

        content = self._format_attempt_log(attempt_log)
        workspace_write_func(str(log_path), content)

    def _format_attempt_log(self, log: CommitAttemptLog) -> str:
        """Format an attempt log as a string.

        Args:
            log: The attempt log to format.

        Returns:
            Formatted string representation.
        """
        lines: list[str] = [
            "=" * 70,
            "COMMIT GENERATION ATTEMPT LOG",
            "=" * 70,
            "",
            f"Attempt:   #{log.attempt_number}",
            f"Agent:     {log.agent}",
            f"Strategy:  {log.strategy}",
            f"Timestamp: {log.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "-" * 70,
            "CONTEXT",
            "-" * 70,
            "",
            f"Prompt size: {log.prompt_size_bytes} bytes ({log.prompt_size_bytes // 1024} KB)",
            f"Diff size:   {log.diff_size_bytes} bytes ({log.diff_size_bytes // 1024} KB)",
            f"Diff truncated: {'YES' if log.diff_was_truncated else 'NO'}",
            "",
        ]

        output_section = log.raw_output if log.raw_output else "[No output captured]"
        lines.extend(
            [
                "-" * 70,
                "RAW AGENT OUTPUT",
                "-" * 70,
                "",
                output_section,
                "",
            ]
        )

        if log.outcome:
            lines.extend(
                [
                    "-" * 70,
                    "OUTCOME",
                    "-" * 70,
                    "",
                    log.outcome,
                    "",
                ]
            )

        lines.append("=" * 70)

        return "\n".join(lines)


def _sanitize_agent_name(agent: str) -> str:
    """Sanitize agent name for use in filename.

    Args:
        agent: Original agent name.

    Returns:
        Sanitized agent name safe for use in filenames.
    """
    sanitized = "".join(c if c.isalnum() else "_" for c in agent)
    return sanitized[:MAX_AGENT_NAME_LENGTH]


__all__ = [
    "CommitAttemptLog",
    "CommitLoggingSession",
]
