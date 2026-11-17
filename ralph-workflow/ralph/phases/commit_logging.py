from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.phases.commit_attempt_log import CommitAttemptLog

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_AGENT_NAME_LENGTH = 20


@dataclass
class CommitLoggingSession:
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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = Path(base_log_dir) / f"run_{timestamp}"

        if not workspace_exists_func(run_dir):
            workspace_makedirs_func(run_dir)

        return cls(run_dir=run_dir, attempt_counter=0, is_noop=False)

    @classmethod
    def noop(cls) -> CommitLoggingSession:
        return cls(
            run_dir=Path("/dev/null/ralph-noop-session"),
            attempt_counter=0,
            is_noop=True,
        )

    def next_attempt_number(self) -> int:
        self.attempt_counter = self.attempt_counter + 1
        return self.attempt_counter

    def new_attempt(self, agent: str, strategy: str) -> CommitAttemptLog:
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
        if self.is_noop:
            return

        sanitized_agent = sanitize_agent_name(attempt_log.agent)
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


def sanitize_agent_name(agent: str) -> str:
    sanitized = "".join(c if c.isalnum() else "_" for c in agent)
    return sanitized[:MAX_AGENT_NAME_LENGTH]


__all__ = [
    "CommitAttemptLog",
    "CommitLoggingSession",
]
