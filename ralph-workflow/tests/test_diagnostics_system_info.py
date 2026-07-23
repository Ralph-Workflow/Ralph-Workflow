"""Unit tests for the diagnostics module.

Tests cover:
- SystemInfo.gather() populates all fields
- run_diagnostics() returns a DiagnosticReport
- AgentDiagnostics with mocked AgentRegistry
- Agent availability check logic
"""

from __future__ import annotations

import pytest

from ralph.diagnostics import (
    SystemInfo,
)
from tests._diagnostics_git_probe import stub_git_probe

MULTI_AGENT_COUNT = 2


class TestSystemInfo:
    """Tests for SystemInfo dataclass."""

    @pytest.fixture(scope="class")
    def info(self) -> SystemInfo:
        """Gather once with a deterministic git probe."""
        return SystemInfo.gather(git_probe=stub_git_probe)

    def test_system_info_gather_returns_instance(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() returns a SystemInfo instance."""
        assert isinstance(info, SystemInfo)

    def test_system_info_gather_populates_os(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates the os field."""
        assert info.os is not None
        assert isinstance(info.os, str)
        assert info.os in {"linux", "darwin", "win32", "cygwin"}

    def test_system_info_gather_populates_arch(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates the arch field."""
        assert info.arch is not None
        assert isinstance(info.arch, str)

    def test_system_info_gather_populates_working_directory(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates working_directory."""
        assert info.working_directory is not None
        assert isinstance(info.working_directory, str)

    def test_system_info_gather_populates_shell(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates shell (or None)."""
        # shell may be None in CI environments, but the field should exist
        assert info.shell is None or isinstance(info.shell, str)

    def test_system_info_gather_uses_injected_env_for_shell(self) -> None:
        """Test that SystemInfo.gather() uses injected env for shell."""
        info = SystemInfo.gather(env={"SHELL": "/bin/zsh"}, git_probe=stub_git_probe)
        assert info.shell == "/bin/zsh"

    def test_system_info_gather_returns_none_shell_when_env_empty(self) -> None:
        """Test that SystemInfo.gather() returns None shell when env is empty."""
        info = SystemInfo.gather(env={}, git_probe=stub_git_probe)
        assert info.shell is None

    def test_system_info_gather_populates_git_version(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates git_version."""
        # git_version may be None if git is not installed
        assert info.git_version is None or isinstance(info.git_version, str)

    def test_system_info_gather_populates_git_repo(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates git_repo as a bool."""
        assert isinstance(info.git_repo, bool)

    def test_system_info_gather_populates_git_branch(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates git_branch when in repo."""
        # git_branch may be None if not in a git repo or git command fails
        if info.git_repo:
            # If we're in a git repo, branch should be populated (or None on detached HEAD)
            assert info.git_branch is None or isinstance(info.git_branch, str)

    def test_system_info_gather_populates_uncommitted_changes(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates uncommitted_changes."""
        # uncommitted_changes may be None if not in a git repo
        if info.git_repo:
            assert isinstance(info.uncommitted_changes, int)
            assert info.uncommitted_changes >= 0
