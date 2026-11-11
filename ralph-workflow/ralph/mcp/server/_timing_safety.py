
from __future__ import annotations

import os

from ralph.mcp.protocol.env import MCP_REQUEST_TIMEOUT_MS_ENV
from ralph.mcp.transport import claude as _claude_transport
from ralph.mcp.transport import codex as _codex_transport
from ralph.mcp.transport import kimi as _kimi_transport
from ralph.mcp.transport import opencode as _opencode_transport
from ralph.mcp.transport import pi as _pi_transport
from ralph.timeout_defaults import (
    EXEC_MAX_TIMEOUT_MS,
    EXEC_SOFT_REPLY_BUDGET_MS,
    MCP_DISPATCH_TIMEOUT_SECONDS,
    SSE_DRAIN_CEILING_MS,
)
from ralph.timeout_defaults import (
    KILL_ESCALATION_CEILING_MS as _KILL_ESCALATION_CEILING_MS,
)

DEFAULT_CLIENT_REQUEST_TIMEOUT_MS: int = 330_000

CLIENT_REQUEST_TIMEOUT_MS: int = int(
    os.environ.get(MCP_REQUEST_TIMEOUT_MS_ENV, str(DEFAULT_CLIENT_REQUEST_TIMEOUT_MS))
)

DISPATCH_CAP_MS: int = EXEC_MAX_TIMEOUT_MS

DRAIN_CEILING_MS: int = SSE_DRAIN_CEILING_MS

KILL_ESCALATION_CEILING_MS: int = _KILL_ESCALATION_CEILING_MS

SERVER_WORST_CASE_MS: int = DISPATCH_CAP_MS + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS

TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = 30_000

TIMING_SAFETY_TRANSPORT_FLOOR_MS: int = (
    EXEC_SOFT_REPLY_BUDGET_MS + TIMING_SAFETY_TRANSPORT_HEADROOM_MS
)


def verify_transport_timeout_safety(timeout_ms: int | None) -> bool:
    if timeout_ms is None:
        return False
    return timeout_ms >= TIMING_SAFETY_TRANSPORT_FLOOR_MS


if not SERVER_WORST_CASE_MS < CLIENT_REQUEST_TIMEOUT_MS:
    raise RuntimeError(
        f"dispatch_cap exceeds client_request_timeout: "
        f"SERVER_WORST_CASE_MS={SERVER_WORST_CASE_MS} "
        f"({DISPATCH_CAP_MS} dispatch + {DRAIN_CEILING_MS} drain "
        f"+ {KILL_ESCALATION_CEILING_MS} kill) "
        f">= CLIENT_REQUEST_TIMEOUT_MS={CLIENT_REQUEST_TIMEOUT_MS}"
    )
if not (
    MCP_DISPATCH_TIMEOUT_SECONDS * 1000 + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS
    < CLIENT_REQUEST_TIMEOUT_MS
):
    raise RuntimeError(
        "dispatch deadline plus drain and kill exceeds client_request_timeout: "
        f"MCP_DISPATCH_TIMEOUT_SECONDS={MCP_DISPATCH_TIMEOUT_SECONDS}"
    )
if TIMING_SAFETY_TRANSPORT_HEADROOM_MS <= 0:
    raise RuntimeError(
        "TIMING_SAFETY_TRANSPORT_HEADROOM_MS must be positive; "
        f"got {TIMING_SAFETY_TRANSPORT_HEADROOM_MS}"
    )
if not EXEC_SOFT_REPLY_BUDGET_MS > 0:
    raise RuntimeError(
        f"EXEC_SOFT_REPLY_BUDGET_MS must be positive; got {EXEC_SOFT_REPLY_BUDGET_MS}"
    )


def _check_transport_timeout(transport_name: str, timeout_ms: int) -> None:
    if timeout_ms < TIMING_SAFETY_TRANSPORT_FLOOR_MS:
        raise RuntimeError(
            f"transport {transport_name} timeout {timeout_ms} ms is below the "
            f"timing-safety floor {TIMING_SAFETY_TRANSPORT_FLOOR_MS} ms "
            f"(EXEC_SOFT_REPLY_BUDGET_MS={EXEC_SOFT_REPLY_BUDGET_MS} + "
            f"TIMING_SAFETY_TRANSPORT_HEADROOM_MS={TIMING_SAFETY_TRANSPORT_HEADROOM_MS})"
        )


_check_transport_timeout("codex", _codex_transport._CODEX_TOOL_TIMEOUT_SEC * 1000)
_check_transport_timeout("kimi", _kimi_transport._KIMI_TOOL_TIMEOUT_MS)
_check_transport_timeout("claude", _claude_transport._CLAUDE_TOOL_TIMEOUT_MS)
_check_transport_timeout("opencode", _opencode_transport._OPENCODE_MCP_CLIENT_TIMEOUT_MS)
_check_transport_timeout("pi", _pi_transport.MCP_TOOL_CALL_DEADLINE_MS)


def timing_safety_margin_ms() -> int:
    return CLIENT_REQUEST_TIMEOUT_MS - SERVER_WORST_CASE_MS


__all__ = [
    "CLIENT_REQUEST_TIMEOUT_MS",
    "DEFAULT_CLIENT_REQUEST_TIMEOUT_MS",
    "DISPATCH_CAP_MS",
    "DRAIN_CEILING_MS",
    "KILL_ESCALATION_CEILING_MS",
    "SERVER_WORST_CASE_MS",
    "TIMING_SAFETY_TRANSPORT_FLOOR_MS",
    "TIMING_SAFETY_TRANSPORT_HEADROOM_MS",
    "timing_safety_margin_ms",
    "verify_transport_timeout_safety",
]
