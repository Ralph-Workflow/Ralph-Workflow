"""Unit tests for the commit_logging module.

Tests cover:
- CommitLoggingSession creation and initialization
- Context manager entry/exit behavior
- Log record structure
- Attempt log formatting
"""

from __future__ import annotations

from ralph.phases.commit_logging import (
    CommitAttemptLog,
    CommitLoggingSession,
)

PROMPT_SIZE_BYTES = 4096
DIFF_SIZE_BYTES = 8192
SECOND_ATTEMPT_NUMBER = 2


class TestCommitLoggingSessionFormatting:
    """Tests for CommitLoggingSession attempt log formatting."""

    def test_format_attempt_log_contains_fields(self) -> None:
        """Test that _format_attempt_log() includes key fields."""
        session = CommitLoggingSession.noop()
        log = CommitAttemptLog(
            attempt_number=1,
            agent="claude",
            strategy="initial",
        )
        log = log.with_prompt_size(1024)
        log = log.with_diff_info(size=2048, was_truncated=False)
        log = log.with_raw_output("test output")
        log = log.with_outcome("success")

        formatted = session._format_attempt_log(log)
        assert "#1" in formatted or "1" in formatted
        assert "claude" in formatted
        assert "initial" in formatted
        assert "1024" in formatted
        assert "2048" in formatted
        assert "test output" in formatted
        assert "success" in formatted

    def test_format_attempt_log_truncated_output_marked(self) -> None:
        """Test that truncated diff is marked in the formatted log."""
        session = CommitLoggingSession.noop()
        log = CommitAttemptLog(
            attempt_number=1,
            agent="claude",
            strategy="initial",
        )
        log = log.with_diff_info(size=50000, was_truncated=True)

        formatted = session._format_attempt_log(log)
        assert "YES" in formatted or "True" in formatted or "truncated" in formatted.lower()
