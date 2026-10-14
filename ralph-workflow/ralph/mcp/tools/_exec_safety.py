"""Command-safety and blacklist checking policy for :mod:`ralph.mcp.tools.exec`.

Housed in a private companion module so :mod:`ralph.mcp.tools.exec` stays
strictly under the 1000-line cap enforced by the repository-structure audit.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.tools._exec_vcs_scanner import check_version_control
from ralph.mcp.tools.coordination import CapabilityDeniedError

_KILL_SIGNAL_ARG_COUNT = 2
_ARCHIVE_EXTENSIONS = (".tar", ".zip", ".gz", ".bz2", ".xz")
_ARCHIVE_EXTRACT_FLAGS = ("-x", "--extract", "-d", "--delete")

_BLACKLIST_DESCRIPTIONS = {
    "privilege_escalation": "privilege escalation",
    "destructive_system": "destructive system operation",
    "network_exfiltration": "network/exfiltration",
    "container_escape": "container/VM escape",
    "multi_file_operation": "multi-file operation",
    "version_control": "version control",
}

_PRIVILEGE_ESCALATION_COMMANDS = {"sudo", "su", "doas", "pkexec", "runuser"}
_DESTRUCTIVE_SYSTEM_COMMANDS = {"shutdown", "reboot", "halt", "poweroff", "killall"}
_NETWORK_TUNNEL_COMMANDS = {"nc", "ncat", "netcat", "socat"}
_REMOTE_NETWORK_COMMANDS = {"ssh", "scp", "rsync"}
_CONTAINER_COMMANDS = {"docker", "podman", "chroot", "nsenter", "unshare"}


def check_command(command: str, args: list[str]) -> str | None:
    """Return a denial reason when a command matches the blacklist policy."""
    cmd = command.strip()
    if not cmd:
        return None

    for checker in (
        check_privilege_escalation,
        check_destructive_system,
        check_network_exfiltration,
        check_container_escape,
        check_multi_file_operation,
        check_version_control,
    ):
        reason = checker(cmd, args)
        if reason:
            return reason
    return None


def _description(key: str) -> str:
    return _BLACKLIST_DESCRIPTIONS.get(key, "operation")


def _command_key(command: str) -> str:
    return Path(command.strip()).name.lower()


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


__all__ = [
    "_ARCHIVE_EXTENSIONS",
    "_ARCHIVE_EXTRACT_FLAGS",
    "_BLACKLIST_DESCRIPTIONS",
    "_CONTAINER_COMMANDS",
    "_DESTRUCTIVE_SYSTEM_COMMANDS",
    "_KILL_SIGNAL_ARG_COUNT",
    "_NETWORK_TUNNEL_COMMANDS",
    "_PRIVILEGE_ESCALATION_COMMANDS",
    "_REMOTE_NETWORK_COMMANDS",
    "apply_exec_policy",
    "check_command",
    "check_container_escape",
    "check_destructive_system",
    "check_multi_file_operation",
    "check_network_exfiltration",
    "check_privilege_escalation",
]
