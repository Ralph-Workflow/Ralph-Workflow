"""Unit tests for the commit_logging module.

Tests cover:
- CommitLoggingSession creation and initialization
- Context manager entry/exit behavior
- Log record structure
- Attempt log formatting
"""

from __future__ import annotations

from datetime import datetime

from ralph.phases.commit_logging import (
    CommitAttemptLog,
)

PROMPT_SIZE_BYTES = 4096
DIFF_SIZE_BYTES = 8192
SECOND_ATTEMPT_NUMBER = 2


class TestCommitAttemptLog:
    """Tests for CommitAttemptLog dataclass."""

    def test_commit_attempt_log_creation(self) -> None:
        """Test that CommitAttemptLog can be created with required fields."""
        log = CommitAttemptLog(
            attempt_number=1,
            agent="claude",
            strategy="initial",
        )
        assert log.attempt_number == 1
        assert log.agent == "claude"
        assert log.strategy == "initial"
        assert log.prompt_size_bytes == 0
        assert log.diff_size_bytes == 0
        assert log.diff_was_truncated is False
        assert log.raw_output is None
        assert log.outcome is None

    def test_commit_attempt_log_with_prompt_size(self) -> None:
        """Test with_prompt_size() method."""
        log = CommitAttemptLog(attempt_number=1, agent="claude", strategy="initial")
        result = log.with_prompt_size(PROMPT_SIZE_BYTES)
        assert result is log  # Should return self for chaining
        assert log.prompt_size_bytes == PROMPT_SIZE_BYTES

    def test_commit_attempt_log_with_diff_info(self) -> None:
        """Test with_diff_info() method."""
        log = CommitAttemptLog(attempt_number=1, agent="claude", strategy="initial")
        result = log.with_diff_info(size=DIFF_SIZE_BYTES, was_truncated=True)
        assert result is log
        assert log.diff_size_bytes == DIFF_SIZE_BYTES
        assert log.diff_was_truncated is True

    def test_commit_attempt_log_with_raw_output(self) -> None:
        """Test with_raw_output() method truncates large output."""
        log = CommitAttemptLog(attempt_number=1, agent="claude", strategy="initial")
        # Small output should not be truncated
        small_output = "small output"
        log.with_raw_output(small_output)
        assert log.raw_output == small_output

    def test_commit_attempt_log_with_raw_output_truncation(self) -> None:
        """Test that large raw output is truncated."""
        log = CommitAttemptLog(attempt_number=1, agent="claude", strategy="initial")
        large_output = "x" * 100000  # 100KB
        log.with_raw_output(large_output)
        assert log.raw_output is not None
        assert len(log.raw_output) < len(large_output)
        assert "[... truncated" in log.raw_output

    def test_commit_attempt_log_with_outcome(self) -> None:
        """Test with_outcome() method."""
        log = CommitAttemptLog(attempt_number=1, agent="claude", strategy="initial")
        result = log.with_outcome("success")
        assert result is log
        assert log.outcome == "success"

    def test_commit_attempt_log_timestamp_default(self) -> None:
        """Test that timestamp defaults to current time."""
        log = CommitAttemptLog(attempt_number=1, agent="claude", strategy="initial")
        assert log.timestamp is not None
        assert isinstance(log.timestamp, datetime)
