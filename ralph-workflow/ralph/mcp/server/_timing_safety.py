"""End-to-end timing safety invariant for the production MCP transport.

The relationship that keeps the system safe is: the server's worst-case
resolution time is strictly less than the client's request timeout. This
is a guarded, tested property — the sum of the three bounded constants
(DISPATCH_CAP_MS + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS) must
be < CLIENT_REQUEST_TIMEOUT_MS, both at import time and at runtime.

A complementary invariant lands in U-TRANSPORT: every transport Ralph
writes an MCP client timeout for MUST be ≥ ``EXEC_SOFT_REPLY_BUDGET_MS``
plus headroom. Clients Ralph cannot size (Cursor, Nanocoder) are
recorded as "no documented timeout" rather than waived: the timing-safety
table is the audit surface, and that row naming the absence is the
proof a future agent can read.

The invariant is enforced via ``if``/``raise RuntimeError`` (NOT
``assert``) so ``python -O`` cannot strip it. The constants themselves
are imported from :mod:`ralph.timeout_defaults` so the production and
test layers cannot drift.
"""

from __future__ import annotations

import os

from ralph.mcp.protocol.env import MCP_REQUEST_TIMEOUT_MS_ENV
from ralph.timeout_defaults import (
    EXEC_MAX_TIMEOUT_MS,
    EXEC_SOFT_REPLY_BUDGET_MS,
    MCP_DISPATCH_TIMEOUT_SECONDS,
    SSE_DRAIN_CEILING_MS,
)
from ralph.timeout_defaults import (
    KILL_ESCALATION_CEILING_MS as _KILL_ESCALATION_CEILING_MS,
)

#: Default client request timeout (milliseconds). 330_000 = 5 minutes +
#: 30 seconds of margin over the server's worst-case. Override via
#: RALPH_MCP_REQUEST_TIMEOUT_MS.
DEFAULT_CLIENT_REQUEST_TIMEOUT_MS: int = 330_000

#: Effective client request timeout (milliseconds). Read from
#: RALPH_MCP_REQUEST_TIMEOUT_MS at import time; falls back to the
#: built-in default.
CLIENT_REQUEST_TIMEOUT_MS: int = int(
    os.environ.get(MCP_REQUEST_TIMEOUT_MS_ENV, str(DEFAULT_CLIENT_REQUEST_TIMEOUT_MS))
)

#: Worst-case dispatch cap. Equal to EXEC_MAX_TIMEOUT_MS — the longest a
#: single exec call may run.
DISPATCH_CAP_MS: int = EXEC_MAX_TIMEOUT_MS

#: Worst-case SSE drain ceiling. The post-final-frame grace the server
#: gives the connection to flush its last write.
DRAIN_CEILING_MS: int = SSE_DRAIN_CEILING_MS

#: Worst-case kill escalation ceiling. The SIGTERM-then-SIGKILL grace
#: before a child is hard-killed. The canonical value lives in
#: ``ralph.timeout_defaults``; this module-local name is the public
#: surface for the timing-safety group.
KILL_ESCALATION_CEILING_MS: int = _KILL_ESCALATION_CEILING_MS

#: End-to-end timing safety margin. The server's worst case (sum of the
#: three constants above) must remain strictly less than the client's
#: request timeout — otherwise a slow client can hit its request
#: timeout while the server is still dispatching, producing the
#: ``-32001`` retry storm the architecture is built to prevent.
SERVER_WORST_CASE_MS: int = DISPATCH_CAP_MS + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS

#: Per-transport timing-safety headroom (milliseconds). Every timeout
#: Ralph writes into an MCP client config (Codex ``tool_timeout_sec``,
#: Kimi ``toolTimeoutMs``, Claude per-server ``timeout``, OpenCode
#: ``experimental.mcp_timeout``, Pi bridge-side ``tools/call``
#: deadline) must be at least ``EXEC_SOFT_REPLY_BUDGET_MS +
#: _TIMING_SAFETY_TRANSPORT_HEADROOM_MS``. Matches the 30 s headroom
#: the per-transport constants adopt (``_CODEX_TOOL_TIMEOUT_HEADROOM_*``,
#: ``_KIMI_TOOL_TIMEOUT_HEADROOM_MS``, ``_CLAUDE_TOOL_TIMEOUT_HEADROOM_MS``,
#: ``_OPENCODE_MCP_CLIENT_TIMEOUT_HEADROOM_MS``, ``MCP_TOOL_CALL_HEADROOM_MS``).
_TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = 30_000
TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = _TIMING_SAFETY_TRANSPORT_HEADROOM_MS

#: Floor (milliseconds) the timing-safety check verifies every
#: transport-configured timeout against. Clients Ralph cannot
#: programmatically size (Cursor, Nanocoder) sit at "n/a" below; this
#: constant is the line those rows have to clear only when Ralph
#: actually writes a timeout for them.
TIMING_SAFETY_TRANSPORT_FLOOR_MS: int = (
    EXEC_SOFT_REPLY_BUDGET_MS + _TIMING_SAFETY_TRANSPORT_HEADROOM_MS
)


def verify_transport_timeout_safety(timeout_ms: int | None) -> bool:
    """Return ``True`` iff a transport-configured timeout is safety-positive.

    A timeout is safety-positive when it is `not None` and is at least
    :data:`TIMING_SAFETY_TRANSPORT_FLOOR_MS` (``EXEC_SOFT_REPLY_BUDGET_MS`` +
    headroom). Clients with no documented tool-call timeout (Cursor,
    Nanocoder) record ``None`` here — Ralph writes no timeout for them,
    so the check returns ``False`` and the row is annotated ``n/a`` in
    the cited transport-timeout table.

    Use this from tests to assert the per-transport constants meet the
    floor; the production import-time invariant below runs the same
    check against the floor itself.
    """
    if timeout_ms is None:
        return False
    return timeout_ms >= TIMING_SAFETY_TRANSPORT_FLOOR_MS


# Import-time invariant (NOT assert) — survives python -O.
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
if _TIMING_SAFETY_TRANSPORT_HEADROOM_MS <= 0:
    raise RuntimeError(
        "_TIMING_SAFETY_TRANSPORT_HEADROOM_MS must be positive; "
        f"got {_TIMING_SAFETY_TRANSPORT_HEADROOM_MS}"
    )
if not EXEC_SOFT_REPLY_BUDGET_MS > 0:
    raise RuntimeError(
        f"EXEC_SOFT_REPLY_BUDGET_MS must be positive; got {EXEC_SOFT_REPLY_BUDGET_MS}"
    )


def timing_safety_margin_ms() -> int:
    """Return the safety margin in milliseconds (client timeout minus server worst case)."""
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
    "_TIMING_SAFETY_TRANSPORT_HEADROOM_MS",
    "timing_safety_margin_ms",
    "verify_transport_timeout_safety",
]
