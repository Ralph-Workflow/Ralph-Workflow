"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import (
    check_package_manager,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestCheckPackageManager:
    def test_apt_install_is_blacklisted(self) -> None:
        reason = check_package_manager("apt", ["install", "vim"])
        assert reason is not None
        assert "apt" in reason.lower()

    def test_yum_install_is_blacklisted(self) -> None:
        reason = check_package_manager("yum", ["install", "vim"])
        assert reason is not None

    def test_pip_install_user_is_blacklisted(self) -> None:
        reason = check_package_manager("pip", ["install", "--user", "requests"])
        assert reason is not None
        assert "pip" in reason.lower()

    def test_pip_install_global_is_blacklisted(self) -> None:
        reason = check_package_manager("pip3", ["install", "-g", "requests"])
        assert reason is not None

    def test_npm_install_global_is_blacklisted(self) -> None:
        reason = check_package_manager("npm", ["install", "-g", "lodash"])
        assert reason is not None

    def test_cargo_install_is_blacklisted(self) -> None:
        reason = check_package_manager("cargo", ["install", "ripgrep"])
        assert reason is not None

    def test_gem_install_global_is_blacklisted(self) -> None:
        reason = check_package_manager("gem", ["install", "rake"])
        assert reason is not None

    def test_gem_install_user_is_allowed(self) -> None:
        reason = check_package_manager("gem", ["install", "--user-install", "rake"])
        assert reason is None

    def test_apt_update_is_blacklisted(self) -> None:
        # "update" is in the blocked flags list
        reason = check_package_manager("apt", ["update"])
        assert reason is not None
