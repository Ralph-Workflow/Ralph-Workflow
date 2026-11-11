from __future__ import annotations

import pytest

from ralph.diagnostics import (
    SystemInfo,
)
from tests._diagnostics_git_probe import stub_git_probe

MULTI_AGENT_COUNT = 2


class TestSystemInfo:
    @pytest.fixture(scope="class")
    def info(self) -> SystemInfo:
        return SystemInfo.gather(git_probe=stub_git_probe)

    def test_system_info_gather_returns_instance(self, info: SystemInfo) -> None:
        assert isinstance(info, SystemInfo)

    def test_system_info_gather_populates_os(self, info: SystemInfo) -> None:
        assert info.os is not None
        assert isinstance(info.os, str)
        assert info.os in {"linux", "darwin", "win32", "cygwin"}

    def test_system_info_gather_populates_arch(self, info: SystemInfo) -> None:
        assert info.arch is not None
        assert isinstance(info.arch, str)

    def test_system_info_gather_populates_working_directory(self, info: SystemInfo) -> None:
        assert info.working_directory is not None
        assert isinstance(info.working_directory, str)

    def test_system_info_gather_populates_shell(self, info: SystemInfo) -> None:
        assert info.shell is None or isinstance(info.shell, str)

    def test_system_info_gather_uses_injected_env_for_shell(self) -> None:
        info = SystemInfo.gather(env={"SHELL": "/bin/zsh"}, git_probe=stub_git_probe)
        assert info.shell == "/bin/zsh"

    def test_system_info_gather_returns_none_shell_when_env_empty(self) -> None:
        info = SystemInfo.gather(env={}, git_probe=stub_git_probe)
        assert info.shell is None

    def test_system_info_gather_populates_git_version(self, info: SystemInfo) -> None:
        assert info.git_version is None or isinstance(info.git_version, str)

    def test_system_info_gather_populates_git_repo(self, info: SystemInfo) -> None:
        assert isinstance(info.git_repo, bool)

    def test_system_info_gather_populates_git_branch(self, info: SystemInfo) -> None:
        if info.git_repo:
            assert info.git_branch is None or isinstance(info.git_branch, str)

    def test_system_info_gather_populates_uncommitted_changes(self, info: SystemInfo) -> None:
        if info.git_repo:
            assert isinstance(info.uncommitted_changes, int)
            assert info.uncommitted_changes >= 0
