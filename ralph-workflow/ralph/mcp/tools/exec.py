"""MCP exec tool handler.

Ports the Rust MCP `exec` tool so agents can execute bounded subprocesses
from the workspace root after capability checks and blacklist filtering.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolError,
    ToolResult,
    require_capability,
)
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Mapping

PROCESS_EXEC_BOUNDED_CAPABILITY = "ProcessExecBounded"
_DEFAULT_TIMEOUT_MS = 30_000
_TIMEOUT_NOTE_THRESHOLD_MS = 60_000
_KILL_SIGNAL_ARG_COUNT = 2
_ARCHIVE_EXTENSIONS = (".tar", ".zip", ".gz", ".bz2", ".xz")
_ARCHIVE_EXTRACT_FLAGS = ("-x", "--extract", "-d", "--delete")

_BLACKLIST_DESCRIPTIONS = {
    "version_control": "version control system",
    "privilege_escalation": "privilege escalation",
    "destructive_system": "destructive system operation",
    "network_exfiltration": "network/exfiltration",
    "package_manager": "package manager",
    "container_escape": "container/VM escape",
    "multi_file_operation": "multi-file operation",
}

_VERSION_CONTROL_COMMANDS = {"git", "svn", "hg", "fossil", "bzr", "darcs"}
_PRIVILEGE_ESCALATION_COMMANDS = {"sudo", "su", "doas", "pkexec", "runuser"}
_DESTRUCTIVE_SYSTEM_COMMANDS = {"shutdown", "reboot", "halt", "poweroff", "killall"}
_NETWORK_TUNNEL_COMMANDS = {"nc", "ncat", "netcat", "socat"}
_REMOTE_NETWORK_COMMANDS = {"ssh", "scp", "rsync"}
_CONTAINER_COMMANDS = {"docker", "podman", "chroot", "nsenter", "unshare"}
_PACKAGE_MANAGERS = {"apt", "yum", "dnf", "pacman", "brew"}

type CwdProvider = Callable[[], Path]


@dataclass(frozen=True)
class _CompletedProcessAdapter:
    """Adapter exposing stdout/stderr/returncode like subprocess.CompletedProcess."""

    stdout: bytes
    stderr: bytes
    returncode: int


type CommandRunner = Callable[[list[str], Path, float | None], _CompletedProcessAdapter]


class ExecutionError(ToolError):
    """Raised when the exec subprocess cannot be started or times out."""


@dataclass(frozen=True)
class ExecParams:
    """Parsed parameters for the MCP exec tool."""

    command: str
    args: list[str]
    timeout_ms: int


@dataclass(frozen=True)
class ExecRunDeps:
    runner: CommandRunner | None = None
    cwd_provider: CwdProvider | None = None


@runtime_checkable
class WorkspaceWithRoot(Protocol):
    """Workspace surface required for command execution."""

    @property
    def root(self) -> Path:
        """Return the absolute workspace root path."""
        ...


def parse_exec_params(params: Mapping[str, object]) -> ExecParams:
    """Parse and validate exec tool parameters."""
    command_value = params.get("command")
    if not isinstance(command_value, str):
        raise InvalidParamsError("Missing 'command' parameter")

    args_value = params.get("args")
    args = (
        [value for value in args_value if isinstance(value, str)]
        if isinstance(args_value, list)
        else []
    )

    timeout_value = params.get("timeout_ms", _DEFAULT_TIMEOUT_MS)
    timeout_ms = (
        timeout_value
        if isinstance(timeout_value, int) and timeout_value >= 0
        else _DEFAULT_TIMEOUT_MS
    )

    return ExecParams(command=command_value, args=args, timeout_ms=timeout_ms)


def check_command(command: str, args: list[str]) -> str | None:
    """Return a denial reason when a command matches the blacklist policy."""
    cmd = command.strip()
    if not cmd:
        return None

    for checker in (
        check_version_control,
        check_privilege_escalation,
        check_destructive_system,
        check_network_exfiltration,
        check_package_manager,
        check_container_escape,
        check_multi_file_operation,
    ):
        reason = checker(cmd, args)
        if reason:
            return reason
    return None


def _description(key: str) -> str:
    return _BLACKLIST_DESCRIPTIONS.get(key, "operation")


def _command_key(command: str) -> str:
    return command.strip().lower()


def _lower_args(args: list[str]) -> list[str]:
    return [arg.lower() for arg in args]


def _contains_any(arg_list: list[str], targets: set[str]) -> bool:
    return any(arg in targets for arg in arg_list)


def check_version_control(command: str, _args: list[str]) -> str | None:
    key = _command_key(command)
    if key in _VERSION_CONTROL_COMMANDS:
        desc = _description("version_control")
        return (
            f"Command '{command}' is blacklisted: {desc} commands must go through "
            "Ralph's git capabilities"
        )
    return None


def check_privilege_escalation(command: str, _args: list[str]) -> str | None:
    key = _command_key(command)
    if key in _PRIVILEGE_ESCALATION_COMMANDS:
        desc = _description("privilege_escalation")
        return f"Command '{command}' is blacklisted: {desc} is not allowed"
    return None


def check_destructive_system(command: str, args: list[str]) -> str | None:
    key = _command_key(command)
    args_lower = _lower_args(args)
    desc = _description("destructive_system")

    if _is_destructive_rm(key, args, args_lower):
        return f"Command 'rm' with recursive force flag targeting root/home is blacklisted: {desc}"

    if key in {"mkfs", "dd"} and any(
        arg.startswith("/dev/") or "of=/dev/" in arg for arg in args_lower
    ):
        return f"Command '{command}' targeting devices is blacklisted: {desc}"

    if key in _DESTRUCTIVE_SYSTEM_COMMANDS:
        return f"Command '{command}' is blacklisted: {desc} is not allowed"

    if _is_init_kill(key, args_lower):
        return f"Command 'kill -9 1' (init) is blacklisted: {desc} is not allowed"

    return None


def _is_destructive_rm(key: str, args: list[str], args_lower: list[str]) -> bool:
    return (
        key == "rm"
        and _contains_any(args_lower, {"-rf", "-r", "-f"})
        and any(
            target == "/"
            or target.startswith("/.")
            or target.startswith("~")
            or target.startswith("/home")
            for target in args
        )
    )


def _is_init_kill(key: str, args_lower: list[str]) -> bool:
    return (
        key == "kill"
        and len(args_lower) >= _KILL_SIGNAL_ARG_COUNT
        and args_lower[0] == "-9"
        and args_lower[1] == "1"
    )


def check_network_exfiltration(command: str, args: list[str]) -> str | None:
    key = _command_key(command)
    args_lower = _lower_args(args)

    if key in {"curl", "wget"}:
        if any(_is_external_url(arg) for arg in args):
            desc = _description("network_exfiltration")
            return (
                f"Command '{command}' to external URLs is blacklisted: {desc} risk. "
                "Use Ralph's HTTP capabilities instead."
            )
        return None

    if key in _NETWORK_TUNNEL_COMMANDS:
        desc = _description("network_exfiltration")
        return f"Command '{command}' is blacklisted: {desc} is not allowed"

    if key in _REMOTE_NETWORK_COMMANDS:
        joined = " ".join(args_lower)
        if "@" in joined or ":/" in joined or "::" in joined:
            desc = _description("network_exfiltration")
            return f"Command '{command}' to remote hosts is blacklisted: {desc} is not allowed"
    return None


def _is_external_url(arg: str) -> bool:
    token = arg.strip()
    if not token or token.startswith("-"):
        return False
    lower = token.lower()
    if "localhost" in lower or "127.0.0.1" in lower:
        return False
    return lower.startswith("http://") or lower.startswith("https://") or "://" in lower


def check_package_manager(command: str, args: list[str]) -> str | None:
    key = _command_key(command)
    args_lower = _lower_args(args)
    desc = _description("package_manager")

    if key in _PACKAGE_MANAGERS and any(
        flag in args_lower for flag in ("install", "update", "upgrade", "remove", "-s", "--sync")
    ):
        return (
            f"Command '{command}' with install/update is blacklisted: {desc} "
            "operations require Ralph's approval"
        )

    if (
        key in {"pip", "pip3"}
        and "install" in args_lower
        and any(flag in args_lower for flag in ("--user", "-g", "--global"))
    ):
        return (
            f"Command '{key} install --user/-g' is blacklisted: {desc} operations "
            "require Ralph's approval"
        )

    if key == "npm" and "install" in args_lower and "-g" in args_lower:
        return (
            f"Command 'npm install -g' is blacklisted: {desc} operations require Ralph's approval"
        )

    if key == "cargo" and args_lower and args_lower[0] == "install":
        return f"Command 'cargo install' is blacklisted: {desc} operations require Ralph's approval"

    if key == "gem" and "install" in args_lower and "--user-install" not in args_lower:
        return (
            "Command 'gem install' (global) is blacklisted: "
            f"{desc} operations require Ralph's approval"
        )

    return None


def check_container_escape(command: str, _args: list[str]) -> str | None:
    key = _command_key(command)
    if key in _CONTAINER_COMMANDS:
        desc = _description("container_escape")
        return f"Command '{command}' is blacklisted: {desc} is not allowed"
    return None


def check_multi_file_operation(command: str, args: list[str]) -> str | None:
    key = _command_key(command)
    args_lower = _lower_args(args)
    desc = _description("multi_file_operation")

    checks = (
        (
            key == "find" and any(flag in args_lower for flag in ("-exec", "-delete")),
            "Command 'find' with -exec/-delete is blacklisted: "
            f"{desc} must go through Ralph's workspace write",
        ),
        (
            key == "xargs"
            and any(flag in args_lower for flag in ("rm", "mv", "cp", "chmod", "chown")),
            "Command 'xargs' with destructive commands is blacklisted: "
            f"{desc} must go through Ralph's workspace write",
        ),
        (
            key == "sed" and "-i" in args_lower,
            f"Command 'sed -i' is blacklisted: {desc} must go through Ralph's workspace write",
        ),
        (
            key == "awk" and ("-i" in args_lower or "-inplace" in args_lower),
            f"Command 'awk -i' is blacklisted: {desc} must go through Ralph's workspace write",
        ),
        (
            key in {"rename", "mmv"},
            f"Command '{command}' is blacklisted: {desc} must go through Ralph's workspace write",
        ),
        (
            key in {"chmod", "chown"} and any(flag in args_lower for flag in ("-r", "-R")),
            "Command '"
            f"{command} -R' is blacklisted: {desc} must go through Ralph's workspace write",
        ),
        (
            _has_recursive_glob_copy(key, args, args_lower),
            "Command '"
            f"{command}' with recursive glob is blacklisted: "
            f"{desc} must go through Ralph's workspace write",
        ),
        (
            _extracts_archive_in_place(key, args_lower),
            "Command '"
            f"{command}' extracting archives in-place is blacklisted: "
            f"{desc} must go through Ralph's workspace write",
        ),
    )
    for applies, message in checks:
        if applies:
            return message
    return None


def _has_recursive_glob_copy(key: str, args: list[str], args_lower: list[str]) -> bool:
    if key not in {"cp", "mv"}:
        return False
    has_glob = any("*" in arg or "?" in arg for arg in args)
    has_recursive = any(flag in args_lower for flag in ("-r", "-rf", "-R", "-f"))
    return has_glob and has_recursive


def _extracts_archive_in_place(key: str, args_lower: list[str]) -> bool:
    if key not in {"tar", "zip", "unzip"}:
        return False
    has_extract_flag = any(
        any(flag in arg for flag in _ARCHIVE_EXTRACT_FLAGS) for arg in args_lower
    )
    has_archive = any(arg.endswith(ext) for arg in args_lower for ext in _ARCHIVE_EXTENSIONS)
    return has_extract_flag and has_archive


def apply_exec_policy(command: str, args: list[str]) -> None:
    """Apply command policy and raise if the command is denied."""
    reason = check_command(command, args)
    if reason is None:
        return
    raise CapabilityDeniedError(f"Command '{command}' denied by policy: {reason}")


def _workspace_root(workspace: object, *, cwd_provider: CwdProvider = Path.cwd) -> Path:
    if isinstance(workspace, WorkspaceWithRoot):
        return workspace.root
    root_value = cast("Path | str | None", getattr(workspace, "root", None))
    if isinstance(root_value, Path):
        return root_value
    if isinstance(root_value, str):
        return Path(root_value)
    return cwd_provider()


def run_command(
    command: str,
    args: list[str],
    workspace: object,
    timeout_ms: int,
    deps: ExecRunDeps | None = None,
) -> _CompletedProcessAdapter:
    """Execute a subprocess in the workspace root."""
    resolved_deps = deps or ExecRunDeps()
    cwd_provider = resolved_deps.cwd_provider or Path.cwd
    command_runner = resolved_deps.runner or _run_subprocess
    cwd = _workspace_root(workspace, cwd_provider=cwd_provider)
    timeout_seconds = timeout_ms / 1000 if timeout_ms > 0 else None
    try:
        return command_runner([command, *args], cwd, timeout_seconds)
    except FileNotFoundError as exc:
        raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc
    except PermissionError as exc:
        raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(
            f"Failed to execute '{command}': timed out after {timeout_ms}ms"
        ) from exc
    except OSError as exc:
        raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc


def _run_subprocess(
    command: list[str], cwd: Path, timeout_seconds: float | None
) -> _CompletedProcessAdapter:
    handle = get_process_manager().spawn(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        label=f"mcp-exec:{command[0]}",
    )
    try:
        stdout, stderr = handle.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        handle.terminate(grace_period_s=0)
        raise
    return _CompletedProcessAdapter(
        stdout=stdout or b"",
        stderr=stderr or b"",
        returncode=handle.returncode or 0,
    )


def format_exec_result(
    command: str,
    args: list[str],
    output: _CompletedProcessAdapter,
    timeout_ms: int,
) -> str:
    """Format subprocess output to match the Rust tool response."""
    stdout = output.stdout.decode("utf-8", errors="replace")
    stderr = output.stderr.decode("utf-8", errors="replace")
    exit_code = output.returncode
    text = (
        f"Command: {command} {args!r}\n"
        f"Exit code: {exit_code}\n\n"
        f"Stdout:\n{stdout}\n\n"
        f"Stderr:\n{stderr}"
    )
    if 0 < timeout_ms < _TIMEOUT_NOTE_THRESHOLD_MS:
        text = f"{text}\n\nNote: This command had a {timeout_ms}ms timeout"
    return text


def handle_exec_command(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    """Execute a bounded subprocess in the workspace root."""
    require_capability(session, PROCESS_EXEC_BOUNDED_CAPABILITY, "Command execution")
    parsed = parse_exec_params(params)
    apply_exec_policy(parsed.command, parsed.args)
    output = run_command(parsed.command, parsed.args, workspace, parsed.timeout_ms)
    return ToolResult(
        content=[
            ToolContent.text_content(
                format_exec_result(parsed.command, parsed.args, output, parsed.timeout_ms)
            )
        ],
        is_error=output.returncode != 0,
    )


__all__ = [
    "PROCESS_EXEC_BOUNDED_CAPABILITY",
    "ExecParams",
    "ExecutionError",
    "WorkspaceWithRoot",
    "apply_exec_policy",
    "check_command",
    "format_exec_result",
    "handle_exec_command",
    "parse_exec_params",
    "run_command",
]
