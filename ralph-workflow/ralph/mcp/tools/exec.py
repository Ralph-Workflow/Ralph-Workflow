"""MCP exec tool handler.

Ports the Rust MCP `exec` tool so agents can execute bounded subprocesses
inside a resettable private sandbox slot after capability checks and policy filtering.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_params import ExecParams
from ralph.mcp.tools._exec_run_deps import CwdProvider, ExecRunDeps
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.exec_overlay import _get_private_exec_base
from ralph.mcp.tools.exec_sandbox import ExecSandboxManager
from ralph.process.manager import SpawnOptions, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.process.manager import ProcessManager
    from ralph.process.manager._process_manager_types import _PsutilModuleLike, _PsutilProcessLike

PROCESS_EXEC_BOUNDED_CAPABILITY = "ProcessExecBounded"
DEFAULT_TIMEOUT_MS = 30_000
_TIMEOUT_NOTE_THRESHOLD_MS = 60_000
_KILL_SIGNAL_ARG_COUNT = 2
_ARCHIVE_EXTENSIONS = (".tar", ".zip", ".gz", ".bz2", ".xz")
_ARCHIVE_EXTRACT_FLAGS = ("-x", "--extract", "-d", "--delete")
_SHELL_OPERATOR_TOKENS = frozenset({"|", "||", "&&", ";", "&", ">", ">>", "<", "<<"})
_EXEC_USAGE_EXAMPLES = (
    'Examples: {"command": "python -m pytest"}, '
    '{"command": ["python", "-m", "pytest"]}, '
    '{"argv": ["python", "-m", "pytest"]}.'
)

_BLACKLIST_DESCRIPTIONS = {
    "privilege_escalation": "privilege escalation",
    "destructive_system": "destructive system operation",
    "network_exfiltration": "network/exfiltration",
    "package_manager": "package manager",
    "container_escape": "container/VM escape",
    "multi_file_operation": "multi-file operation",
}

_PRIVILEGE_ESCALATION_COMMANDS = {"sudo", "su", "doas", "pkexec", "runuser"}
_DESTRUCTIVE_SYSTEM_COMMANDS = {"shutdown", "reboot", "halt", "poweroff", "killall"}
_NETWORK_TUNNEL_COMMANDS = {"nc", "ncat", "netcat", "socat"}
_REMOTE_NETWORK_COMMANDS = {"ssh", "scp", "rsync"}
_CONTAINER_COMMANDS = {"docker", "podman", "chroot", "nsenter", "unshare"}
_PACKAGE_MANAGERS = {"apt", "yum", "dnf", "pacman", "brew"}


@runtime_checkable
class WorkspaceWithRoot(Protocol):
    """Workspace surface required for command execution."""

    @property
    def root(self) -> Path:
        """Return the absolute workspace root path."""
        ...


def parse_exec_params(params: Mapping[str, object]) -> ExecParams:
    """Parse and validate exec tool parameters."""
    command_tokens = _parse_exec_command_tokens(params)
    args = _parse_exec_args(params.get("args"))
    command = command_tokens[0] if command_tokens else ""
    merged_args = [*command_tokens[1:], *args]

    timeout_value = params.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    timeout_ms = (
        timeout_value
        if isinstance(timeout_value, int) and timeout_value >= 0
        else DEFAULT_TIMEOUT_MS
    )

    return ExecParams(command=command, args=merged_args, timeout_ms=timeout_ms)


def _parse_exec_command_tokens(params: Mapping[str, object]) -> list[str]:
    command_value = params.get("command")
    if isinstance(command_value, str):
        return _parse_shell_words(command_value, field_name="command")
    if isinstance(command_value, list):
        return _coerce_argv_tokens(command_value, field_name="command")
    if command_value is not None:
        raise InvalidParamsError(
            "'command' must be a string or string array. " + _EXEC_USAGE_EXAMPLES
        )

    argv_value = params.get("argv")
    if isinstance(argv_value, str):
        return _parse_shell_words(argv_value, field_name="argv")
    if isinstance(argv_value, list):
        return _coerce_argv_tokens(argv_value, field_name="argv")
    if argv_value is not None:
        raise InvalidParamsError("'argv' must be a string or string array. " + _EXEC_USAGE_EXAMPLES)

    raise InvalidParamsError("Missing 'command' or 'argv' parameter. " + _EXEC_USAGE_EXAMPLES)


def _parse_exec_args(args_value: object) -> list[str]:
    if isinstance(args_value, list):
        return [value for value in args_value if isinstance(value, str)]
    if isinstance(args_value, str):
        return _parse_shell_words(args_value, field_name="args")
    return []


def _coerce_argv_tokens(values: list[object], *, field_name: str) -> list[str]:
    tokens = [value for value in values if isinstance(value, str)]
    if not tokens:
        raise InvalidParamsError(f"{field_name} must include at least one string token")
    if any(token in _SHELL_OPERATOR_TOKENS for token in tokens):
        raise InvalidParamsError(
            f"{field_name} must not use shell control operators: exec does not run a shell. "
            "Pass a plain command and arguments instead."
        )
    return tokens


def _parse_shell_words(value: str, *, field_name: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []

    try:
        lexer = shlex.shlex(stripped, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        raise InvalidParamsError(f"Malformed {field_name} value: {exc}") from exc

    if any(token in _SHELL_OPERATOR_TOKENS for token in tokens):
        raise InvalidParamsError(
            f"{field_name} must not use shell control operators: exec does not run a shell. "
            "Pass a plain command and arguments instead."
        )
    return tokens


def check_command(command: str, args: list[str]) -> str | None:
    """Return a denial reason when a command matches the blacklist policy."""
    cmd = command.strip()
    if not cmd:
        return None

    for checker in (
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


def check_privilege_escalation(command: str, _args: list[str]) -> str | None:
    """Return a denial reason if the command is a privilege escalation tool."""
    key = _command_key(command)
    if key in _PRIVILEGE_ESCALATION_COMMANDS:
        desc = _description("privilege_escalation")
        return f"Command '{command}' is blacklisted: {desc} is not allowed"
    return None


def check_destructive_system(command: str, args: list[str]) -> str | None:
    """Return a denial reason if the command is a destructive system operation."""
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
    """Return a denial reason if the command could exfiltrate data over the network."""
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
    """Return a denial reason if the command invokes a package manager install."""
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
    """Return a denial reason if the command could escape container isolation."""
    key = _command_key(command)
    if key in _CONTAINER_COMMANDS:
        desc = _description("container_escape")
        return f"Command '{command}' is blacklisted: {desc} is not allowed"
    return None


def check_multi_file_operation(command: str, args: list[str]) -> str | None:
    """Return a denial reason if the command performs bulk file operations."""
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
    if isinstance(workspace, Path):
        return workspace
    if isinstance(workspace, str):
        return Path(workspace)

    root_value: object | None = getattr(workspace, "root", None)
    if isinstance(root_value, Path):
        return root_value
    if isinstance(root_value, str):
        return Path(root_value)
    return cwd_provider()


def _rewrite_env_path(value: str, source_root: str, overlay_root: str, os_name: str) -> str:
    if not source_root:
        return value
    if os_name != "nt":
        return value.replace(source_root, overlay_root)

    value_lower = value.lower()
    source_root_lower = source_root.lower()
    rewritten = value
    while True:
        idx = value_lower.find(source_root_lower)
        if idx < 0:
            return rewritten
        rewritten = (
            rewritten[:idx]
            + overlay_root
            + rewritten[idx + len(source_root) :]
        )
        value_lower = rewritten.lower()


def _child_process_env(workspace_root: Path, cwd: Path) -> dict[str, str]:
    source_root = str(workspace_root)
    overlay_root = str(cwd)
    env = {
        key: _rewrite_env_path(value, source_root, overlay_root, os.name)
        for key, value in os.environ.items()
    }
    env["PWD"] = overlay_root
    env.pop("OLDPWD", None)
    return env


def _kill_orphan_tree_windows(root_pid: int, psutil_mod: _PsutilModuleLike | None) -> None:
    """Recursively kill orphaned descendants of root_pid on Windows."""
    if psutil_mod is None or root_pid <= 0:
        return
    frontier: set[int] = {root_pid}
    seen: set[int] = set()
    while frontier:
        children_found: dict[int, _PsutilProcessLike] = {}
        with contextlib.suppress(Exception):
            for proc in psutil_mod.process_iter(["pid", "ppid"]):
                with contextlib.suppress(Exception):
                    info = proc.info
                    ppid = info.get("ppid")
                    pid = proc.pid
                    if ppid in frontier and pid not in seen:
                        children_found[pid] = proc
        if not children_found:
            break
        seen.update(children_found)
        for proc in children_found.values():
            with contextlib.suppress(Exception):
                proc.kill()
        frontier = set(children_found)


def _cleanup_exec_orphans(pgid: int, root_pid: int, psutil_mod: _PsutilModuleLike | None) -> None:
    """Kill orphaned processes after the exec root exits."""
    if hasattr(os, "killpg"):
        if pgid > 1:
            with contextlib.suppress(OSError):
                os.killpg(pgid, signal.SIGKILL)
    else:
        _kill_orphan_tree_windows(root_pid, psutil_mod)


class _SandboxManagerCache:
    instance: ClassVar[ExecSandboxManager | None] = None
    lock: ClassVar[threading.Lock] = threading.Lock()


def _get_sandbox_manager() -> ExecSandboxManager:
    if _SandboxManagerCache.instance is None:
        with _SandboxManagerCache.lock:
            if _SandboxManagerCache.instance is None:
                _SandboxManagerCache.instance = ExecSandboxManager(
                    base_dir=_get_private_exec_base()
                )
    return _SandboxManagerCache.instance


def run_command(
    command: str,
    args: list[str],
    workspace: object,
    timeout_ms: int,
    deps: ExecRunDeps | None = None,
) -> _CompletedProcessAdapter:
    """Execute a subprocess in a private resettable sandbox rooted at the workspace."""
    resolved_deps = deps or ExecRunDeps()
    cwd_provider = resolved_deps.cwd_provider or Path.cwd
    overlay_factory = resolved_deps.overlay_factory or _get_sandbox_manager().acquire
    cwd = _workspace_root(workspace, cwd_provider=cwd_provider)
    timeout_seconds = timeout_ms / 1000 if timeout_ms > 0 else None
    with overlay_factory(cwd) as overlay_cwd:
        try:
            if resolved_deps.runner is not None:
                return resolved_deps.runner([command, *args], overlay_cwd, timeout_seconds)
            return _run_subprocess(
                [command, *args],
                overlay_cwd,
                timeout_seconds,
                cwd,
                resolved_deps.process_manager,
            )
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
    command: list[str],
    cwd: Path,
    timeout_seconds: float | None,
    workspace_root: Path,
    pm: ProcessManager | None = None,
) -> _CompletedProcessAdapter:
    effective_pm = pm if pm is not None else get_process_manager()
    handle = effective_pm.spawn(
        command,
        SpawnOptions(
            cwd=str(cwd),
            env=_child_process_env(workspace_root, cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            label=f"mcp-exec:{command[0]}",
        ),
    )
    stdout: bytes | None = b""
    stderr: bytes | None = b""
    try:
        stdout, stderr = handle.communicate_and_cleanup(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        handle.terminate(grace_period_s=0)
        raise
    finally:
        _cleanup_exec_orphans(handle.record.pgid, handle.record.pid, effective_pm._psutil)
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
    """Execute a bounded subprocess inside a private resettable workspace sandbox."""
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
    "DEFAULT_TIMEOUT_MS",
    "PROCESS_EXEC_BOUNDED_CAPABILITY",
    "ExecParams",
    "ExecRunDeps",
    "ExecutionError",
    "WorkspaceWithRoot",
    "apply_exec_policy",
    "check_command",
    "format_exec_result",
    "handle_exec_command",
    "parse_exec_params",
    "run_command",
]
