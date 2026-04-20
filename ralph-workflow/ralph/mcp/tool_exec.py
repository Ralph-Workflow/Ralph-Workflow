"""Tool exec handlers - compatibility re-exports from sub-package."""

from ralph.mcp.tools.exec import (
    _DEFAULT_TIMEOUT_MS,
    ExecParams,
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

COMMAND_BLACKLIST: tuple[str, ...] = ()


def is_command_allowed(command: str, args: list[str]) -> bool:
    """Return True when the exec policy allows the command."""
    return check_command(command, args) is None


__all__ = [
    "COMMAND_BLACKLIST",
    "_DEFAULT_TIMEOUT_MS",
    "ExecParams",
    "ExecRunDeps",
    "ExecutionError",
    "WorkspaceWithRoot",
    "apply_exec_policy",
    "check_command",
    "check_container_escape",
    "check_destructive_system",
    "check_multi_file_operation",
    "check_network_exfiltration",
    "check_package_manager",
    "check_privilege_escalation",
    "check_version_control",
    "format_exec_result",
    "handle_exec_command",
    "is_command_allowed",
    "parse_exec_params",
    "run_command",
]
