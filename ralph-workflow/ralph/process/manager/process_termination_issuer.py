from enum import StrEnum
from typing import cast


class ProcessTerminationIssuer(StrEnum):
    SUPERVISOR_IDLE = "supervisor_idle"
    QUOTA_DETECTOR = "quota_detector"
    POST_EXIT_WATCHDOG = "post_exit_watchdog"
    RECOVERY_TIMEOUT = "recovery_timeout"
    BROKEN_AGENT_TIMER = "broken_agent_timer"
    OPERATOR = "operator"
    CONTEXT_EXIT = "context_exit"
    CALL_TIMEOUT = "call_timeout"
    OUTPUT_LIMIT = "output_limit"
    INVOKE_TEARDOWN = "invoke_teardown"
    TOOL_TIMEOUT = "tool_timeout"
    MCP_SHUTDOWN = "mcp_shutdown"
    GIT_TIMEOUT = "git_timeout"
    EXECUTOR_CANCEL = "executor_cancel"
    TEST_RUNNER = "test_runner"
    DIAGNOSTICS = "diagnostics"


def unverified_issuer(raw: str) -> ProcessTerminationIssuer:
    return cast("ProcessTerminationIssuer", raw)


__all__ = ["ProcessTerminationIssuer", "unverified_issuer"]
