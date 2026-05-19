"""Unit tests for the commit_logging module.

Tests cover:
- CommitLoggingSession creation and initialization
- Context manager entry/exit behavior
- Log record structure
- Attempt log formatting
"""

from __future__ import annotations

from ralph.phases.commit_logging import (
    MAX_AGENT_NAME_LENGTH,
    sanitize_agent_name,
)

PROMPT_SIZE_BYTES = 4096
DIFF_SIZE_BYTES = 8192
SECOND_ATTEMPT_NUMBER = 2


class TestSanitizeAgentName:
    """Tests for sanitize_agent_name() function."""

    def test_sanitize_agent_name_keeps_alphanumeric(self) -> None:
        """Test that alphanumeric characters are preserved."""
        assert sanitize_agent_name("claude") == "claude"
        assert sanitize_agent_name("opencode") == "opencode"

    def test_sanitize_agent_name_replaces_special_chars(self) -> None:
        """Test that non-alphanumeric characters are replaced with underscores."""
        assert sanitize_agent_name("claude-code") == "claude_code"
        assert sanitize_agent_name("open.code") == "open_code"
        assert sanitize_agent_name("test/agent") == "test_agent"

    def test_sanitize_agent_name_truncates_long_names(self) -> None:
        """Test that agent names longer than MAX_AGENT_NAME_LENGTH are truncated."""
        long_name = "a" * 100
        result = sanitize_agent_name(long_name)
        assert len(result) == MAX_AGENT_NAME_LENGTH

    def test_sanitize_agent_name_max_length(self) -> None:
        """Test that sanitized name is at most MAX_AGENT_NAME_LENGTH characters."""
        result = sanitize_agent_name("claude-code")
        assert len(result) <= MAX_AGENT_NAME_LENGTH
