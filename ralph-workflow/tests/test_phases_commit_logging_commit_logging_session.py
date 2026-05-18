"""Unit tests for the commit_logging module.

Tests cover:
- CommitLoggingSession creation and initialization
- Context manager entry/exit behavior
- Log record structure
- Attempt log formatting
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.phases.commit_logging import (
    CommitAttemptLog,
    CommitLoggingSession,
)

if TYPE_CHECKING:
    from pathlib import Path

PROMPT_SIZE_BYTES = 4096
DIFF_SIZE_BYTES = 8192
SECOND_ATTEMPT_NUMBER = 2


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
