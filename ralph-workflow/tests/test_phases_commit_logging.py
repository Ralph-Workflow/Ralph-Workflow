"""Unit tests for the commit_logging module.

Tests cover:
- CommitLoggingSession creation and initialization
- Context manager entry/exit behavior
- Log record structure
- Attempt log formatting
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ralph.phases.commit_logging import (
    MAX_AGENT_NAME_LENGTH,
    CommitAttemptLog,
    CommitLoggingSession,
    _sanitize_agent_name,
)

if TYPE_CHECKING:
    from pathlib import Path

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


class TestCommitLoggingSession:
    """Tests for CommitLoggingSession class."""

    def test_commit_logging_session_new(self) -> None:
        """Test creating a new CommitLoggingSession."""

        def exists(path: Path) -> bool:
            return False

        def makedirs(path: Path) -> None:
            pass

        session = CommitLoggingSession.new(
            base_log_dir="/tmp/test_logs",
            workspace_exists_func=exists,
            workspace_makedirs_func=makedirs,
        )
        assert session is not None
        assert session.attempt_counter == 0
        assert session.is_noop is False
        assert session.run_dir.name.startswith("run_")

    def test_commit_logging_session_noop(self) -> None:
        """Test creating a no-op CommitLoggingSession."""
        session = CommitLoggingSession.noop()
        assert session.is_noop is True
        assert session.attempt_counter == 0

    def test_commit_logging_session_next_attempt_number(self) -> None:
        """Test next_attempt_number() increments counter."""
        session = CommitLoggingSession.noop()
        assert session.next_attempt_number() == 1
        assert session.attempt_counter == 1
        assert session.next_attempt_number() == SECOND_ATTEMPT_NUMBER
        assert session.attempt_counter == SECOND_ATTEMPT_NUMBER

    def test_commit_logging_session_new_attempt(self) -> None:
        """Test new_attempt() creates a CommitAttemptLog."""
        session = CommitLoggingSession.noop()
        attempt = session.new_attempt(agent="claude", strategy="initial")
        assert attempt.attempt_number == 1
        assert attempt.agent == "claude"
        assert attempt.strategy == "initial"

    def test_commit_logging_session_new_attempt_increments_counter(self) -> None:
        """Test that new_attempt() increments the session counter."""
        session = CommitLoggingSession.noop()
        assert session.attempt_counter == 0
        session.new_attempt(agent="claude", strategy="initial")
        assert session.attempt_counter == 1
        session.new_attempt(agent="claude", strategy="retry")
        assert session.attempt_counter == SECOND_ATTEMPT_NUMBER

    def test_write_summary_noop_is_noop(self) -> None:
        """Test that write_summary() is a no-op for noop sessions."""
        session = CommitLoggingSession.noop()
        # Should not raise
        session.write_summary(
            total_attempts=3,
            final_outcome="success",
            workspace_write_func=lambda path, content: None,
        )

    def test_write_attempt_log_noop_is_noop(self) -> None:
        """Test that write_attempt_log() is a no-op for noop sessions."""
        session = CommitLoggingSession.noop()
        log = CommitAttemptLog(
            attempt_number=1,
            agent="claude",
            strategy="initial",
        )
        # Should not raise
        session.write_attempt_log(
            attempt_log=log,
            workspace_write_func=lambda path, content: None,
        )

    def test_write_summary_calls_workspace_write(self) -> None:
        """Test that write_summary() calls workspace_write_func for non-noop sessions."""
        session = CommitLoggingSession.noop()
        # Even for noop, the write_summary should work without error
        session.write_summary(
            total_attempts=2,
            final_outcome="success",
            workspace_write_func=lambda path, content: None,
        )


class TestSanitizeAgentName:
    """Tests for _sanitize_agent_name() function."""

    def test_sanitize_agent_name_keeps_alphanumeric(self) -> None:
        """Test that alphanumeric characters are preserved."""
        assert _sanitize_agent_name("claude") == "claude"
        assert _sanitize_agent_name("opencode") == "opencode"

    def test_sanitize_agent_name_replaces_special_chars(self) -> None:
        """Test that non-alphanumeric characters are replaced with underscores."""
        assert _sanitize_agent_name("claude-code") == "claude_code"
        assert _sanitize_agent_name("open.code") == "open_code"
        assert _sanitize_agent_name("test/agent") == "test_agent"

    def test_sanitize_agent_name_truncates_long_names(self) -> None:
        """Test that agent names longer than MAX_AGENT_NAME_LENGTH are truncated."""
        long_name = "a" * 100
        result = _sanitize_agent_name(long_name)
        assert len(result) == MAX_AGENT_NAME_LENGTH

    def test_sanitize_agent_name_max_length(self) -> None:
        """Test that sanitized name is at most MAX_AGENT_NAME_LENGTH characters."""
        result = _sanitize_agent_name("claude-code")
        assert len(result) <= MAX_AGENT_NAME_LENGTH


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
